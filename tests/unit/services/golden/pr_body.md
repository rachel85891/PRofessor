Hi! Your API builds, and creating and reading users works.

**Fixed in this PR (marked `//fix:` in the code):**
- Updating with kind 1 also ran kind 2; a `break` was missing, and now it is there.

**Names (C# conventions):**
- In C#, local variables are written in camelCase: **newCount** instead of `New_Count`.

**Files:**
- Class1.cs looks like a leftover from the project template; is it still needed?
