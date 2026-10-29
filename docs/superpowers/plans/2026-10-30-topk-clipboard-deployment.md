# 2026-10-30 Topk clipboard deployment

The user requests a one-time Mac publication using 2026-10-29 signals for 2026-10-30, and explicitly chooses continued clipboard communication with updated QMT and relay support. The previously confirmed account has available cash 244607, zero holdings, and no working orders; the old QMT is stopped and its 1030 inbox batch withdrawn.

## Design and constraints

Keep the existing explicit quantity/limit-price clipboard trade protocol and extend broker observations plus external orchestration. The alternative file bridge already supports the guarded cash contract, but the user chose clipboard. Do not weaken the existing guarded contract to make the old relay accept a new string. New guarded batches require the `topk-cash-v1` capability and complete account-wide working-order evidence.

Wait for every SELL to become terminal, count positive residual holdings, restrict candidates to remaining slots, freeze the cash budget and divisor before quote/lot checks, and persist original requests before submission. Missing observations or unrelated working orders block buying. Preserve frozen allocations, reserved names, and request identities across restarts. Failed candidates leave cash unused. Preserve ordinary legacy batches.

Keep historical capital 300000 and the old ledger/history intact. Initialize the independent strategy ledger from the user-confirmed handoff with an explicit, idempotent cash reconciliation to 244607. Use sequence002 because the previous withdrawn same-day plan used sequence001 and client-order IDs share a day/sequence namespace. Prepare locally and verify the exact batch before exposing the unchanged bytes to the shared inbox. No Windows strategy or broker operation is executed by this session. The one-time publication does not enable the disabled Mac scheduler.

## Implementation and verification

1. QMT worker: extend capability/version reporting and strictly validated positions plus complete working orders. Add failing tests before implementation; preserve Python3.6/ASCII compatibility.
2. Automation worker: validate complete guarded batches and implement durable sell-terminal/cash/slot orchestration. Test failed/partial sells, foreign orders, capability refusal, frozen budgets, and restart identity.
3. Root: run model/parity checks and native signal generation; prepare the Windows bundle and practical setup instructions. Make version/capability visible in relay status.
4. Run affected clipboard and full live-trading regressions, independent review, schema/checksum/ledger validation, then publish verified bytes to the shared bridge and deliver Windows setup commands. Windows round-trip acceptance remains an explicit machine-side check.
