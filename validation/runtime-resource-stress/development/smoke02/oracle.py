"""Literal protocol/state/resource expectations; imports no GoPyt implementation."""


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate(name, facts, config):
    if name == 'slow_clients':
        require(facts['closed'] and facts['calls_before_recovery'] == 0, 'slow input reached handler or survived')
        require(facts['recovery'] == [200, '{"amount":3}'] and facts['calls'] == 1, 'slow-input recovery')
    elif name == 'saturation':
        require(facts['admitted'] == config['http_workers'] + config['http_queue'], 'admission capacity')
        require(facts['queued'] == config['http_queue'], 'queue capacity')
        require(facts['overload'] == [503, ''] and facts['calls_before_recovery'] == 0, 'overload dispatch')
        require(facts['recovery'] == [200, '{"amount":3}'] and facts['calls'] == 1, 'saturation recovery')
    elif name == 'cancellation_storm':
        require(facts['outcomes'] == ['Cancelled'] * config['storm_groups'], 'storm outcomes')
        require(facts['entered'] == facts['reserved'] == config['storm_groups'] * config['storm_arms_per_group'], 'storm occupancy')
        require(facts['active'] == facts['rejected'] == facts['handoffs'] == 0 and facts['recovered'], 'storm cleanup/recovery')
    elif name == 'lock_contention':
        count = config['lock_callers']
        require(facts['blocked'] == count and facts['stopped_before_release'], 'lock admission/cleanup')
        require(facts['outcomes'] == sorted(['Cancelled']*(count//2) + ['Trap6']*(count//2)), 'lock cancellation outcomes')
        require(facts['before_recovery'] == [['balance','10']], 'cancelled operation published')
        require(facts['recovered'] and facts['after_recovery'] == [['balance','9'],['receipt','done']], 'lock recovery state')
        require(facts['pending'] == 0, 'pending storage file')
    elif name == 'shutdown_write':
        require(facts['response'] == [200, ''] and facts['closed'], 'drain acknowledgment')
        require(facts['rows'] == [['first','committed']], 'durable state or pipelined dispatch')
        require(facts['publications'] == 1 and facts['pending'] == facts['workers'] == 0, 'publication/cleanup count')
    else:
        raise AssertionError('unknown phase')


def validate_resources(observed, baseline, config):
    for resource in ('fd', 'thread', 'child'):
        require(observed[resource] - baseline[resource] <= config['maximum_post_gc_' + resource + '_growth'],
                resource + ' count grew after quiescence')
    require(observed['rss'] - baseline['rss'] <= config['maximum_post_gc_rss_growth_bytes'], 'post-GC RSS budget')
