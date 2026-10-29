using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using NamingApi.Models;

namespace NamingApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class User : ControllerBase
    {
        [HttpGet]
        public ActionResult<IEnumerable<LogUser>> GetAll()
        {
            var FilePath = "users.txt";
            if (!System.IO.File.Exists(FilePath))
                return Ok(new List<LogUser>());
            List<LogUser> users = System.IO.File.ReadLines(FilePath)
                .Select(line => JsonSerializer.Deserialize<LogUser>(line)!)
                .ToList();
            return Ok(users);
        }
    }
}
