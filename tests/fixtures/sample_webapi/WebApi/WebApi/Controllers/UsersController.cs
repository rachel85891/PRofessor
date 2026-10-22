using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using WebApi.Models;

namespace WebApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        // The users are kept in a text file next to the project, one JSON object per line.
        private const string FilePath = "users.txt";

        private static List<User> ReadUsers()
        {
            List<User> users = new List<User>();
            foreach (string line in System.IO.File.ReadLines(FilePath))
            {
                if (string.IsNullOrWhiteSpace(line))
                    continue;
                User? user = JsonSerializer.Deserialize<User>(line);
                if (user != null)
                    users.Add(user);
            }
            return users;
        }

        private static void WriteUsers(List<User> users)
        {
            System.IO.File.WriteAllLines(FilePath, users.Select(u => JsonSerializer.Serialize(u)));
        }

        // GET: api/users
        [HttpGet]
        public ActionResult<IEnumerable<User>> Get()
        {
            return Ok(ReadUsers());
        }

        // GET api/users/5
        [HttpGet("{id}")]
        public ActionResult<User> Get(int id)
        {
            User? user = ReadUsers().FirstOrDefault(u => u.Id == id);
            if (user == null)
                return NotFound();
            return Ok(user);
        }

        // POST api/users
        [HttpPost]
        public ActionResult<User> Post([FromBody] User user)
        {
            List<User> users = ReadUsers();
            user.Id = users.Count == 0 ? 1 : users.Max(u => u.Id) + 1;
            System.IO.File.AppendAllText(FilePath, JsonSerializer.Serialize(user) + Environment.NewLine);
            return CreatedAtAction(nameof(Get), new { id = user.Id }, user);
        }

        // POST api/users/login
        [HttpPost("login")]
        public ActionResult<User> Login([FromBody] LoginRequest request)
        {
            User? user = ReadUsers().FirstOrDefault(
                u => u.UserName == request.UserName && u.Password == request.Password);
            if (user == null)
                return Unauthorized();
            return Ok(user);
        }

        // PUT api/users/5
        [HttpPut("{id}")]
        public IActionResult Put(int id, [FromBody] User user)
        {
            List<User> users = ReadUsers();
            int index = users.FindIndex(u => u.Id == id);
            if (index == -1)
                return NotFound();
            user.Id = id;
            users[index] = user;
            WriteUsers(users);
            return NoContent();
        }

        // DELETE api/users/5
        [HttpDelete("{id}")]
        public IActionResult Delete(int id)
        {
            List<User> users = ReadUsers();
            int removed = users.RemoveAll(u => u.Id == id);
            if (removed == 0)
                return NotFound();
            WriteUsers(users);
            return NoContent();
        }
    }
}
