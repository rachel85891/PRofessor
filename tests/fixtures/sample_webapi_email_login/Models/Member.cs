namespace EmailLoginApi.Models
{
    // Users sign up and log in with their e-mail address; there is no user name.
    public class Member
    {
        public int Id { get; set; }
        public string Email { get; set; } = "";
        public string Password { get; set; } = "";
    }

    public class LoginRequest
    {
        public string Email { get; set; } = "";
        public string Password { get; set; } = "";
    }
}
