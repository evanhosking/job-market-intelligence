# Use linkedin-jobs-cli in a deterministic worker

The user explicitly replaced Apify with the supplied local linkedin-jobs-cli. Use its Python client, search builder and normalized posting contract directly; preserve the standalone CLI. Vendor the patched package so this repository installs independently. Do not invoke shell commands from HTTP requests or let an LLM schedule searches.

A synchronous source client is acceptable in a dedicated worker process. HTTP refresh calls return immediately with a durable queued run. No remote dataset/polling/webhook abstraction is simulated. Since no authoritative provider total exists, result caps, repeated pages and failed details remain explicit completeness caveats. Saved checkpoints can be replayed, but a partial live run cannot be upgraded to complete merely by replaying its available rows.
