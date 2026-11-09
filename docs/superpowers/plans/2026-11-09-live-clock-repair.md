# Live clock and quote retry repair

Goal: prevent a transient quote-time failure from permanently skipping the day's guarded sells, while preserving freshness, position limits, and exactly-once requests.

Evidence: Windows SMB system time is 6.043 seconds behind the Mac; three NTP sources are approximately 1.846 seconds ahead of the Mac. Today's saved QMT quote led the Windows observation by 5.489 seconds. The original sell quote was overwritten, so its precise age is unavailable.

Decision: correct Windows time through its existing W32Time service; do not loosen the -5/+180 second quote-age gate or derive trusted time from a single stock tick. Keep the current checkout because the production source and shared deployment use it; unrelated research edits are outside scope.

- [x] Add failing tests for quote-time diagnostics, transient sell retry, mixed healthy/unavailable sells, restart/uncertain response, deadline and old journals.
- [x] Add detailed quote evidence and durable per-order sell retries; only never-submitted orders can be freshly prepared. Existing requests retain their identity/content and session rules.
- [x] Expose relay version and runtime path in status; provide a reusable Windows clock diagnostic/resync command and deployment instructions.
- [x] Run the clipboard and live-trading regression suite, obtain an independent code review, and address material findings. Result: 1460 passed; two material review findings reproduced and fixed.
- [x] Update the authorized shared runtime after verification; confirm Windows task path and loaded source hashes through operator `auto --check` evidence. Result: READY, `2026-11-09-quote-retry-v1`. Preserve state and immutable signal files.
- [x] Locate the failing communication path: same Windows host and Cloudflare IPv4 succeeds twice from ephemeral UDP ports, times out twice from source UDP123, independently of W32Time. Scoped service firewall rules did not help and were removed. The exact dropping device remains unknown.
- [x] Build and review a persistent clock fallback using Windows' working `stripchart /rdtsc` path. Three sources, at least two agreeing within 250 ms, fresh low-delay samples, bounded off-execution-window correction, and fresh post-correction verification. 28 PowerShell checks pass locally; installer reruns them under Windows 5.1.
- [x] Install the protected SYSTEM task at 09:00/14:40/startup, verify actual clock correction and a fresh scheduled execution. Windows PowerShell 5.1 tests passed; the 23:35 run reduced the measured slow-clock offset from 7.8502148 seconds to 0.0047421 seconds, with all three sources agreeing. Independent SMB/NTP comparison at 23:39 also confirmed a sub-second offset. Native W32Time synchronization is not claimed; the fixed-port path remains unresolved.

Completion requires verified relay deployment and Windows clock correction with a verified persistent maintenance task. A copied shared package alone does not prove a separate Windows installation was updated. No trading requests are sent during validation.
