# Blackridge self-hosting v2 experiment

The builder receives only `brief.md` plus normalized live public search observations. It receives
no file, source excerpt, prompt, component registry, or documentation from Blackridge 1.0.

The public repository `krapcys1-maker/blackridge-systems` is a contamination canary. Discovery is
allowed and should be recorded. Its contents, Git objects, packages, and copied text are forbidden
builder inputs and forbidden generated artifacts.

The experiment passes the first vertical-slice gate only when:

1. natural language is converted to a schema-valid capability plan;
2. at least four distinct capabilities are planned;
3. live GitHub search returns exact repository identities for multiple capabilities;
4. the canary search result is recorded separately and excluded from builder material;
5. the isolated builder produces a syntactically valid runnable candidate without canary text;
6. a human inspects the candidate before it receives secrets or executes;
7. a representative second brief produces a structured generated-system proposal and evidence;
8. missing full verification, reuse, repair, and release behavior remains explicitly failed or
   not implemented rather than being inferred from successful model output.

This gate does not establish that v2 is better than v1 or that it can yet construct arbitrary
production systems. Comparison preserves the raw capability checks instead of one weighted score.
