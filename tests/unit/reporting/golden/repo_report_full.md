# Noa Testman and Yael Testberg: hw01

- Student: Noa Testman
- Partner: Yael Testberg
- Repo: https://github.com/noa/webapi
- Commit: 0123456
- Run: 20261029-090000-abc123
- Assignment: hw01
- Status: failed

## Build and run

- Target framework: net8.0 (older than the course's; the student is asked to move to it, and it never lowers the grade)
- Prepare: succeeded; build: succeeded; run: failed
- Failure: The app built but crashed or exited while starting.

## Assignments

- _general: checked
- hw01: checked

## Findings

### Critical (1)

- **hw01-create**: POST creates a user

  POST /api/users: expected 201, got 500.
  The app threw.

### Major (1)

- **hw01-update**: PUT updates a user

  PUT ignores the id in the route.

  Evidence: `Services/UserService.cs`, lines 40-42:

  ```
  users[index] = user;
  // uses ``user.Id``
  Save();
  ```

### Minor (1)

- **gen-naming-methods**: Method names are PascalCase

  The method getUser should be named GetUser.

  Evidence: `Controllers/UsersController.cs`, line 21:

  ```
  public IActionResult getUser(int id)
  ```

## Not evaluated

- **hw01-login**: Login with correct details succeeds. The app did not start or did not answer, so this could not be checked over HTTP.
- **hw01-read-back**: The created user can be read back by id (the data is saved). The request needed a value (such as a new user's id) that an earlier request did not return, so it was not sent.

## Errors

- assignment_hw01: unexpected RuntimeError: boom
