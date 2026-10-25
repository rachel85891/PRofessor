namespace CustomModelApi.Models
{
    // The password is a number, and Name is required: with <Nullable>enable,
    // [ApiController] treats a non-nullable string as required.
    public class Account
    {
        public int Id { get; set; }
        public string UserName { get; set; } = "";
        public int Password { get; set; }
        public string Name { get; set; } = "";
    }

    // Login by e-mail, not by user name.
    public class LoginForm
    {
        public string Email { get; set; } = "";
        public int Password { get; set; }
    }
}
