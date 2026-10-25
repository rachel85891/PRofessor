using Microsoft.AspNetCore.Mvc;
using CustomModelApi.Models;

namespace CustomModelApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class UsersController : ControllerBase
    {
        private static readonly List<Account> Accounts = new List<Account>();

        [HttpPost]
        public ActionResult<Account> Register([FromBody] Account account)
        {
            account.Id = Accounts.Count + 1;
            Accounts.Add(account);
            return Created($"/api/users/{account.Id}", account);
        }

        [HttpPost("login")]
        public ActionResult<Account> Login([FromBody] LoginForm form)
        {
            Account? account = Accounts.FirstOrDefault(
                a => a.UserName == form.Email && a.Password == form.Password);
            return account == null ? Unauthorized() : Ok(account);
        }
    }
}
