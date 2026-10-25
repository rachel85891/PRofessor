using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using TwoControllers.Models;

namespace TwoControllers.Controllers
{
    // The full users API, kept in users.txt.
    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        private const string FilePath = "users.txt";

        private static List<User> ReadUsers()
        {
            return System.IO.File.ReadLines(FilePath)
                .Where(line => !string.IsNullOrWhiteSpace(line))
                .Select(line => JsonSerializer.Deserialize<User>(line)!)
                .ToList();
        }

        private static void WriteUsers(List<User> users)
        {
            System.IO.File.WriteAllLines(FilePath, users.Select(u => JsonSerializer.Serialize(u)));
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

        [HttpDelete("{id}")]
        public IActionResult Delete(int id)
        {
            List<User> users = ReadUsers();
            if (users.RemoveAll(u => u.Id == id) == 0)
                return NotFound();
            WriteUsers(users);
            return NoContent();
        }
    }
}
