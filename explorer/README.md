# explorer

Working branch for rethinking CostGuard. Read in this order:

1. [FINDINGS.md](FINDINGS.md): using today's CostGuard as a builder; where it is thin.
2. [POSITIONING.md](POSITIONING.md): why anyone would bother, and the flow that would make them.
3. [PATH.md](PATH.md): why the regression gate is not the core; unit economics instead.
4. [DEEPDIVE.md](DEEPDIVE.md): a working prototype of the report, tested against planted ground truth.
5. [PRODUCT.md](PRODUCT.md): the synthesis as a product (Guard, Know, Optimize) and what to build first.
6. [WHO-AND-WHY.md](WHO-AND-WHY.md): web research on who would use it and why, what already exists, and how to test demand.
7. [MARKET.md](MARKET.md): competitors, the 2026 consolidation, what users need, and what it changes in the plan.

Try the prototype (standard library only, no install):

```bash
python explorer/saas-sim/simulate.py --out explorer/saas-sim/out      # a synthetic month for a small AI SaaS
python -m unitecon report saas-sim/out/calls.jsonl --plans saas-sim/plans.json   # run from explorer/
python explorer/saas-sim/validate.py                                  # does it find what the simulator planted?
python -m unittest discover -s explorer/tests                         # hand-computed arithmetic checks
```

[sample-report.md](saas-sim/sample-report.md) is that report, ready to read.
