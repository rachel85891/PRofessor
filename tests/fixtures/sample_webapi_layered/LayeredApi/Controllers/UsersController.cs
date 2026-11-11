using Microsoft.AspNetCore.Mvc;
using Repository;

namespace LayeredApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        private readonly UserRepository _repository = new UserRepository();

        [HttpGet]
        public ActionResult<IEnumerable<User>> Get()
        {
            return Ok(_repository.ReadUsers());
        }

        [HttpGet("{id}")]
        public ActionResult<User> Get(int id)
        {
            User? user = _repository.ReadUsers().FirstOrDefault(u => u.Id == id);
            return user == null ? NotFound() : Ok(user);
        }

        [HttpPost]
        public ActionResult<User> Post([FromBody] User user)
        {
            User added = _repository.Add(user);
            return CreatedAtAction(nameof(Get), new { id = added.Id }, added);
        }
    }
}
