Password strength validation should be implemented in several places:

1. **Client-side:** The user can click a button to check the password strength. The server returns a value (1–4), which is then displayed on the screen.
2. **User Registration (POST):** Server-side validation must enforce password strength requirements. If the password has a rating below 3, the system should return a 400 status code along with the error details.
3. **Updating User Details (PUT):** Server-side validation must enforce password strength requirements. If the password has a rating below 3, the system should return a 400 status code along with the error details.

Note that the password strength check is located within a `password service`, which is called by both the `user controller` (for cases 2 and 3) and the `password controller` (for case 1).
