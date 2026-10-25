using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using TwoControllers.Models;

namespace TwoControllers.Controllers
{
    // An older, partial controller with its own data file, user.txt. A user created
    // through UsersController does not exist here.
    [Route("api/[controller]")]
    [ApiController]
    public class UserController : ControllerBase
    {
        private const string FilePath = "user.txt";

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

        [HttpPut("{id}")]
        public IActionResult Update(int id, [FromBody] User user)
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
        public IActionResult Remove(int id)
        {
            List<User> users = ReadUsers();
            if (users.RemoveAll(u => u.Id == id) == 0)
                return NotFound();
            WriteUsers(users);
            return NoContent();
        }

        [HttpPost("login")]
        public ActionResult<User> Login([FromBody] LoginRequest request)
        {
            User? user = ReadUsers().FirstOrDefault(
                u => u.UserName == request.UserName && u.Password == request.Password);
            return user == null ? NotFound() : Ok(user);
        }
    }
}
