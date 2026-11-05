Hi! Your API builds, and creating users works.

**Fixed in this PR (marked `//fix:` in the code):**
- **`case 1` ran `case 2` too.** A `break` was missing; now it is there.
- **`users.json` did not end with a new line.** A new line was added at the end.

**For you to do (marked in the code):**
- **PUT:** The assignment asks PUT to return `Ok()`.

**Names (C# conventions):**
- **`New_Count`:** local variables are written in camelCase: `newCount`.

**Files:**
- Class1.cs looks like a leftover from the project template; is it still needed?
