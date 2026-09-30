# Data

`handbook/` is the same fictional Northwind Labs handbook used by
[production-rag-service](https://github.com/tsriharsha402/production-rag-service), plus one
deliberately hostile file: `team-notes.md` is an "editable by everyone" page containing a
prompt-injection attempt. It exists to test that the agent treats retrieved text as data,
not instructions.
