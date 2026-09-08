"""Trusted reference policy and reproducible bounded acceptance cases."""
import itertools
import random

OBLIGATIONS = {
    'R1': 'Only positive refunds within the remaining paid balance are allowed.',
    'R2': 'Closed orders cannot receive new refunds.',
    'R3': 'A new request must match the current version; increment once.',
    'R4': 'Cumulative refunded value increases by exactly the accepted amount.',
    'R5': 'Customer and order identity are preserved by the trusted adapter.',
    'R6': 'Identical idempotency retries replay without another state change.',
    'R7': 'Ledger change and idempotency record commit together or neither commits.',
    'R8': 'Rules, acceptance cases and executor identity cannot be changed by candidate files.',
}


def reference(args):
    paid, refunded, amount, version, expected, closed = args
    if closed or version != expected or version not in range(1, 10**9):
        return False
    if not 0 <= refunded <= paid <= 10**12 or amount <= 0:
        return False
    return refunded + amount <= paid


def cases():
    result = []
    inputs = list(itertools.product([0,1,2,100,10**12], [0,1,100],
                                    [0,1,2,100,10**12], [1,2], [1,2], [False,True]))
    inputs += [(100,0,1,v,v,False) for v in [-1,0,10**9-1,10**9]]
    inputs += [(-1,0,1,1,1,False),(10**12+1,0,1,1,1,False),(100,-1,1,1,1,False)]
    rng = random.Random(90210)
    for _ in range(200):
        paid = rng.randint(1,10**12)
        refunded = rng.randint(0,paid)
        inputs.append((paid,refunded,rng.randint(1,paid+1),1,1,False))
    for args in inputs:
        valid = reference(args)
        result.append({'symbol': 'refund_policy.allowed', 'args': list(args), 'expected': valid})
    for paid,refunded,amount in [(100,0,1),(100,1,99),(10**12,10**12-1,1),
                                  (0,0,1),(100,0,101),(100,-1,1),(-1,0,1),
                                  (10**12+1,0,1),(100,0,0)]:
        case = {'symbol': 'refund_policy.after_refund', 'args': [paid,refunded,amount]}
        if 0 <= refunded <= paid <= 10**12 and 0 < amount <= paid-refunded:
            case['expected'] = sum([refunded,amount])
        else:
            case['trap'] = 1
        result.append(case)
    return result
