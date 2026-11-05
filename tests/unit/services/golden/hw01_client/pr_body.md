Hi! Your API builds, and creating users works.

**Fixed in this PR (marked `//fix:` and `<!--fix: … -->` in the code):**
- **`case 1` ran `case 2` too.** A `break` was missing; now it is there.
- **`users.json` did not end with a new line.** A new line was added at the end.
- **`update.js` looked for `#firstname`, but the input's id was `name`.** The id in `update.html` is now `firstname`.
- **`if (res.status = 200)` set the status instead of comparing it.** It is now `===`.

**For you to do (marked in the code):**
- **PUT:** The assignment asks PUT to return `Ok()`.
- **`res.json()`:** it returns a Promise; add `await`.

**Names (C# and JavaScript conventions):**
- **`New_Count`:** local variables are written in camelCase: `newCount`.
- **`user_data`:** JavaScript variables are written in camelCase: `userData`.

**Files:**
- Class1.cs looks like a leftover from the project template; is it still needed?
