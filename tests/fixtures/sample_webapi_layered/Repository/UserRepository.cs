using System.Text.Json;

namespace Repository
{
    public class User
    {
        public int Id { get; set; }
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }

    public class UserRepository
    {
        // A path that only exists on the developer's own machine.
        private const string FilePath = @"C:\Users\Developer\source\repos\Layered\Repository\users.txt";

        public List<User> ReadUsers()
        {
            return File.ReadLines(FilePath)
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .Select(line => JsonSerializer.Deserialize<User>(line)!)
                .ToList();
        }

        public User Add(User user)
        {
            List<User> users = ReadUsers();
            user.Id = users.Count == 0 ? 1 : users.Max(u => u.Id) + 1;
            File.AppendAllText(FilePath, JsonSerializer.Serialize(user) + "\n");
            return user;
        }
    }
}
