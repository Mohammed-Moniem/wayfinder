# Assumption Ledger

| ID | Assumption | Impact (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) | Confidence | Evidence | Status | Destination blocking | Blocks / affects | Revalidate when |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Allowed statuses: `OPEN`, `VALIDATED`, `REFUTED`, `ACCEPTED-RISK`, `SUPERSEDED`.

## Accepted-risk receipts

Required for every `HIGH` or `CRITICAL` assumption marked `ACCEPTED-RISK`.

| Receipt | Assumption | Accepted by | Authority source | Accepted at | Exact scope | Expiry / revalidate when | Rationale |
| --- | --- | --- | --- | --- | --- | --- | --- |

An agent inference, silence, prior unrelated approval, or generated artifact is not acceptance.

## Refutation receipts

Required when a destination-blocking `HIGH` or `CRITICAL` assumption is `REFUTED` and an affected Decision remains settled instead of being reopened or superseded.

| Assumption | Decision | Outcome | Evidence | Actor | Timestamp | Decision revision |
| --- | --- | --- | --- | --- | --- | --- |
