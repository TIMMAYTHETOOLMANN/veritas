#!/usr/bin/env python3
"""Quick functional test for ScanResult wrapper and telemetry model."""
from core.scan_result import ScanResult
from core.opportunity_telemetry import Candidate, CandidateStatus, RejectionReason

# Test 1: Empty ScanResult
sr = ScanResult()
assert not sr, 'Empty ScanResult should be falsy'
assert len(sr) == 0, 'Empty ScanResult should have length 0'
print('PASS: empty ScanResult is falsy and has length 0')

# Test 2: ScanResult with edges
sr2 = ScanResult(edges=[{'net_margin': 0.05}, {'net_margin': 0.03}])
assert sr2, 'ScanResult with edges should be truthy'
assert len(sr2) == 2
assert sr2[0]['net_margin'] == 0.05
print('PASS: ScanResult with edges works')

# Test 3: Iteration
items = list(sr2)
assert len(items) == 2
print('PASS: iteration works')

# Test 4: Why zero report
sr3 = ScanResult()
sr3.statistics = {'pairs_discovered': 5, 'quotes_attempted': 20, 'valid_quotes': 18, 'cross_venue_candidates': 3}
sr3.record_rejection(RejectionReason.INSUFFICIENT_SPREAD)
sr3.record_rejection(RejectionReason.INSUFFICIENT_SPREAD)
sr3.record_rejection(RejectionReason.FEE_REJECTION)
report = sr3.generate_why_zero_report()
assert 'insufficient spread' in report
print('PASS: why_zero_report works')

# Test 5: to_legacy_tuple
sr4 = ScanResult(edges=[{'net_margin': 0.05}])
sr4.candidates = [Candidate(net_profit_usd=0.05, route='WETH/USDC/WETH', buy_venue='Sushi', sell_venue='Uni')]
edges, legacy_report = sr4.to_legacy_tuple()
assert len(edges) == 1
assert len(legacy_report) == 1
print('PASS: to_legacy_tuple works')

# Test 6: Candidate status transitions
c = Candidate(status=CandidateStatus.CANDIDATE, net_profit_usd=-0.01)
c.reject(RejectionReason.GAS_REJECTION)
assert c.status == CandidateStatus.REJECTED
assert c.rejection_reason == RejectionReason.GAS_REJECTION
print('PASS: Candidate reject() works')

# Test 7: Why zero report with best candidate
sr5 = ScanResult()
sr5.statistics = {'pairs_discovered': 10, 'quotes_attempted': 40, 'valid_quotes': 35, 'cross_venue_candidates': 5}
sr5.candidates = [
    Candidate(route='WETH/USDC/WETH', buy_venue='Sushi', sell_venue='Uni',
             input_amount=1.0, gross_profit_usd=0.013, swap_fees_usd=0.003,
             flash_loan_fee_usd=0.0003, estimated_gas_usd=0.004,
             safety_margin_usd=0.002, net_profit_usd=0.0037),
]
sr5.record_rejection(RejectionReason.INSUFFICIENT_SPREAD)
report5 = sr5.generate_why_zero_report()
assert 'BEST OBSERVED' in report5
assert 'Sushi' in report5
print('PASS: why_zero_report with best candidate works')

print()
print('ALL TESTS PASSED')
