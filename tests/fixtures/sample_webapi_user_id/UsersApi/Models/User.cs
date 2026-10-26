namespace UsersApi.Models
{
    // The key is called UserId, so the JSON responses carry "userId", not "id".
    public class User
    {
        public int UserId { get; set; }
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }
}
