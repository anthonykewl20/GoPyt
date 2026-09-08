# Initial editor integration

Configure a client to launch `gopyt-lsp` over stdio with a local `rootUri`. Supported methods: initialize/shutdown, full-document open/change/close, diagnostics, document formatting, document symbols and basic current-document completion. Positions use UTF-16. Unsaved overlays never rewrite project sources or its lock.

Snapshots support dependency-free packages, at most 256 discovered project files and 2 MB combined source. Checker subprocesses have a five-second CPU limit and eight-second wall timeout; Linux also limits address space to 512 MB. Closing an overlay recomputes diagnostics. Stale document versions are ignored.

Diagnostics currently report the first compiler error with line-level placement. Completion is declaration-based, not type-directed. Definition, references, rename, multi-root workspaces and dependency graphs remain future work. Source parsing for formatting and symbols remains in the editor process; use trusted local projects.
