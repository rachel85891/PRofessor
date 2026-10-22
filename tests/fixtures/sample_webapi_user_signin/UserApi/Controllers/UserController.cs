using System.Text.Json;
using Microsoft.AspNetCore.Mvc;

namespace UserApi.Controllers
{
    public class User
    {
        public int Id { get; set; }
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }

    public class SignInRequest
    {
        public string UserName { get; set; } = "";
        public string Password { get; set; } = "";
    }

    // Singular controller name, so the routes are /api/user..., and "signin" instead of "login".
    [Route("api/[controller]")]
    [ApiController]
    public class UserController : ControllerBase
    {
        private const string FilePath = "users.txt";

        private static List<User> ReadUsers()
        {
            return System.IO.File.ReadLines(FilePath)
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .Select(line => JsonSerializer.Deserialize<User>(line)!)
                .ToList();
        }

        [HttpGet]
        public ActionResult<IEnumerable<User>> GetAll()
        {
            return Ok(ReadUsers());
        }

        [HttpGet("{id}", Name = "GetUser")]
        public ActionResult<User> GetById(int id)
        {
            User? user = ReadUsers().FirstOrDefault(u => u.Id == id);
            return user == null ? NotFound() : Ok(user);
        }

        [HttpPost]
        public ActionResult<User> Add([FromBody] User user)
        {
            List<User> users = ReadUsers();
            user.Id = users.Count == 0 ? 1 : users.Max(u => u.Id) + 1;
            System.IO.File.AppendAllText(FilePath, JsonSerializer.Serialize(user) + "\n");
            return CreatedAtAction(nameof(GetById), new { id = user.Id }, user);
        }

        [HttpPost ("signin")]
        public ActionResult<User> SignIn([FromBody] SignInRequest request)
        {
            User? user = ReadUsers().FirstOrDefault(
                u => u.UserName == request.UserName && u.Password == request.Password);
            return user == null ? NotFound() : Ok(user);
        }
    }
}
