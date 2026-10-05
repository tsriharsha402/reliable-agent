# Data

`handbook/` is the same fictional Northwind Labs handbook used by
[production-rag-service](https://github.com/tsriharsha402/production-rag-service), plus one
deliberately hostile files: `team-notes.md` and `engineering-wiki-laptops.md` are
"editable by everyone" pages containing prompt-injection attempts (booking PTO silently;
exfiltrating a balance into a ticket). It exists to test that the agent treats retrieved text as data,
not instructions.
