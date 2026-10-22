using System.Text.Json;
using Microsoft.AspNetCore.Mvc;

namespace AbsolutePathApi.Controllers
{
    public class User
    {
        public int Id { get; set; }
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }

    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        // A path that only exists on the developer's own machine.
        private const string FilePath = @"C:\Users\Developer\source\repos\AbsolutePathApi\Data\users.txt";

        private static List<User> ReadUsers()
        {
            return System.IO.File.ReadLines(FilePath)
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .Select(line => JsonSerializer.Deserialize<User>(line)!)
                .ToList();
        }

        [HttpGet]
        public ActionResult<IEnumerable<User>> Get()
        {
            return Ok(ReadUsers());
        }

        [HttpGet("{id}")]
        public ActionResult<User> Get(int id)
        {
            User? user = ReadUsers().FirstOrDefault(u => u.Id == id);
            return user == null ? NotFound() : Ok(user);
        }

        [HttpPost]
        public ActionResult<User> Post([FromBody] User user)
        {
            List<User> users = ReadUsers();
            user.Id = users.Count == 0 ? 1 : users.Max(u => u.Id) + 1;
            System.IO.File.AppendAllText(FilePath, JsonSerializer.Serialize(user) + "\n");
            return CreatedAtAction(nameof(Get), new { id = user.Id }, user);
        }
    }
}
