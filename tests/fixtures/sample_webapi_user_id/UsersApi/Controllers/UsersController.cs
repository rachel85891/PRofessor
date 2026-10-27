using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using UsersApi.Models;

namespace UsersApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        private const string FilePath = "users.txt";

        private static List<User> Load()
        {
            return System.IO.File.ReadLines(FilePath)
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .Select(line => JsonSerializer.Deserialize<User>(line)!)
                .ToList();
        }

        private static void Save(List<User> users)
        {
            System.IO.File.WriteAllLines(FilePath, users.Select(u => JsonSerializer.Serialize(u)));
        }

        [HttpGet]
        public ActionResult<IEnumerable<User>> GetAll()
        {
            return Ok(Load());
        }

        [HttpGet("{userId}")]
        public ActionResult<User> GetOne(int userId)
        {
            User? user = Load().FirstOrDefault(u => u.UserId == userId);
            return user == null ? NotFound() : Ok(user);
        }

        [HttpPost]
        public ActionResult<User> Create([FromBody] User user)
        {
            List<User> users = Load();
            user.UserId = users.Count == 0 ? 1 : users.Max(u => u.UserId) + 1;
            System.IO.File.AppendAllText(FilePath, JsonSerializer.Serialize(user) + "\n");
            return CreatedAtAction(nameof(GetOne), new { userId = user.UserId }, user);
        }

        [HttpPost("login")]
        public ActionResult<User> Login([FromBody] User credentials)
        {
            User? user = Load().FirstOrDefault(
                u => u.UserName == credentials.UserName && u.Password == credentials.Password);
            return user == null ? NoContent() : Ok(user);
        }

        [HttpPut("{userId}")]
        public IActionResult Update(int userId, [FromBody] User user)
        {
            List<User> users = Load();
            int index = users.FindIndex(u => u.UserId == userId);
            if (index == -1)
                return NotFound();
            user.UserId = userId;
            users[index] = user;
            Save(users);
            return NoContent();
        }

        [HttpDelete("{userId}")]
        public IActionResult Remove(int userId)
        {
            List<User> users = Load();
            if (users.RemoveAll(u => u.UserId == userId) == 0)
                return NotFound();
            Save(users);
            return NoContent();
        }
    }
}
