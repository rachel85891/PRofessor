using Microsoft.AspNetCore.Mvc;

namespace NoIdApi.Controllers
{
    public class User
    {
        public int Id { get; set; }
        public string UserName { get; set; } = "";
    }

    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        private static readonly List<User> Users = new List<User>();

        // Saves the user but answers without a body or a Location header,
        // so nothing tells the caller which id it got.
        [HttpPost]
        public IActionResult Add([FromBody] User user)
        {
            user.Id = Users.Count + 1;
            Users.Add(user);
            return Ok();
        }

        [HttpGet("{id}")]
        public ActionResult<User> Get(int id)
        {
            User? user = Users.FirstOrDefault(u => u.Id == id);
            return user == null ? NotFound() : Ok(user);
        }
    }
}
