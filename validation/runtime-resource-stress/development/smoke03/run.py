"""Freeze inputs, execute stress episodes, and retain raw facts and resource data."""
import argparse
import contextlib
import copy
import gc
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import cases
import oracle
from gopyt.toolchain import FINGERPRINT

HERE = Path(__file__).resolve().parent


def snapshot():
    rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    return dict(rss=rss, fd=len(list(Path('/proc/self/fd').iterdir())),
                thread=len(threading.enumerate()), child=len(multiprocessing.active_children()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('this campaign measures Linux /proc resource counters')
    config = json.loads((HERE/'protocol.json').read_text())
    source_paths = sorted(list((ROOT/'gopyt').glob('*.py')) + list(HERE.glob('*.py')) + [HERE/'protocol.json'])
    relative = [str(p.relative_to(ROOT)) for p in source_paths]
    dirty = subprocess.check_output(['git','status','--porcelain','--',*relative], cwd=ROOT, text=True)
    if dirty and not args.smoke:
        parser.error('commit frozen workload and runtime sources before a measured run')
    source_hashes = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    args.output.mkdir(parents=True, exist_ok=False)
    identity = dict(mode='smoke' if args.smoke else 'measured', config=config, source_sha256=source_hashes,
                    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    dirty_source=dirty, runtime_sha256=FINGERPRINT.hex(), python=sys.version,
                    platform=platform.platform(), fd_limits=resource.getrlimit(resource.RLIMIT_NOFILE))
    (args.output/'inputs.json').write_text(json.dumps(identity,indent=2)+'\n')
    samples, peak, stop, lock = [], {}, threading.Event(), threading.Lock()
    def mark():
        observation = snapshot()
        with lock:
            for key,value in observation.items():
                peak[key] = max(peak.get(key, 0), value)
        return observation
    def sampler():
        while not stop.wait(config['sample_seconds']):
            try:
                mark()
            except BaseException as error:
                samples.append(str(error))
                return
    monitor = threading.Thread(target=sampler, name='resource-sampler')
    monitor.start()
    phases, controls = [], []
    failure = None
    try:
        with (args.output/'episodes.jsonl').open('w') as raw, (args.output/'execution.log').open('w') as execution:
            for name in config['phases']:
                function = getattr(cases, name)
                warmups = 0 if args.smoke else config['warmup_episodes']
                for i in range(warmups):
                    with contextlib.redirect_stdout(execution), contextlib.redirect_stderr(execution):
                        facts = function(config, i, mark)
                    oracle.validate(name, facts, config)
                gc.collect()
                baseline = snapshot()
                latencies, resources = [], []
                started, iteration = time.monotonic(), 0
                minimum = 2 if args.smoke else config['minimum_measured_episodes_per_phase']
                seconds = 0 if args.smoke else config['minimum_measured_seconds_per_phase']
                with lock:
                    peak.clear()
                print('starting ' + name, flush=True)
                while iteration < minimum or time.monotonic() - started < seconds:
                    begin = time.perf_counter_ns()
                    with contextlib.redirect_stdout(execution), contextlib.redirect_stderr(execution):
                        facts = function(config, iteration, mark)
                    elapsed = time.perf_counter_ns() - begin
                    before_gc = snapshot()
                    gc.collect()
                    after_gc = snapshot()
                    row = dict(phase=name, iteration=iteration, elapsed_ns=elapsed, facts=facts,
                               before_gc=before_gc, after_gc=after_gc)
                    raw.write(json.dumps(row,sort_keys=True)+'\n')
                    raw.flush()
                    oracle.validate(name, facts, config)
                    oracle.validate_resources(after_gc, baseline, config)
                    oracle.require(elapsed <= config['maximum_episode_seconds']*1e9, 'episode latency budget')
                    if iteration == 0:
                        corrupted = copy.deepcopy(facts)
                        key = {'slow_clients':'closed','saturation':'admitted','cancellation_storm':'active',
                               'lock_contention':'before_recovery','shutdown_write':'rows'}[name]
                        corrupted[key] = None if key in ('before_recovery','rows') else (-1 if key != 'closed' else False)
                        rejected = False
                        try:
                            oracle.validate(name, corrupted, config)
                        except AssertionError:
                            rejected = True
                        oracle.require(rejected, 'semantic negative control accepted')
                        controls.append(dict(phase=name, mutation=key, rejected=rejected))
                    latencies.append(elapsed)
                    resources.append(after_gc)
                    iteration += 1
                sorted_ns = sorted(latencies)
                def percentile(p):
                    return sorted_ns[max(0, int((len(sorted_ns)-1)*p))]
                with lock:
                    observed_peak = dict(peak)
                phases.append(dict(phase=name, episodes=iteration, seconds=time.monotonic()-started,
                                   baseline=baseline, final=resources[-1], sampled_peak=observed_peak,
                                   p50_ns=percentile(.5), p95_ns=percentile(.95), p99_ns=percentile(.99),
                                   max_ns=max(latencies), mean_ns=statistics.mean(latencies)))
                print('passed ' + name + ': ' + str(iteration) + ' episodes', flush=True)
        invalid = dict(baseline, fd=baseline['fd']+1)
        try:
            oracle.validate_resources(invalid, baseline, config)
        except AssertionError:
            controls.append(dict(phase='resources', mutation='extra fd', rejected=True))
        else:
            raise AssertionError('resource negative control accepted')
        oracle.require(not samples, 'resource sampler failed: '+repr(samples))
        oracle.require(all(hashlib.sha256(p.read_bytes()).hexdigest() == source_hashes[str(p.relative_to(ROOT))]
                           for p in source_paths), 'source changed during campaign')
    except BaseException:
        failure = traceback.format_exc()
        (args.output/'failure.txt').write_text(failure)
    finally:
        stop.set()
        monitor.join(5)
        report = dict(status='failed' if failure else 'passed', mode=identity['mode'], phases=phases,
                      negative_controls=controls, sampler_errors=samples,
                      maxrss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
                      scope=config['scope'])
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if failure:
        print(failure, file=sys.stderr)
        return 1
    print(json.dumps(report,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
