using Microsoft.AspNetCore.Mvc;
using EmailLoginApi.Models;

namespace EmailLoginApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class MembersController : ControllerBase
    {
        private static readonly List<Member> Members = new List<Member>();

        [HttpPost]
        public ActionResult<Member> SignUp([FromBody] Member member)
        {
            member.Id = Members.Count + 1;
            Members.Add(member);
            return Created($"/api/members/{member.Id}", member);
        }

        [HttpPost("login")]
        public ActionResult<Member> Login([FromBody] LoginRequest request)
        {
            Member? member = Members.FirstOrDefault(
                m => m.Email == request.Email && m.Password == request.Password);
            return member == null ? Unauthorized() : Ok(member);
        }
    }
}
