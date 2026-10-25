using Microsoft.AspNetCore.Mvc;
using LoginAttributesApi.Models;

namespace LoginAttributesApi.Controllers
{
    [Route("api/[controller]")]
    [ApiController]
    public class CustomersController : ControllerBase
    {
        private static readonly List<Customer> Customers = new List<Customer>();

        [HttpPost]
        public ActionResult<Customer> Register([FromBody] Customer customer)
        {
            customer.Id = Customers.Count + 1;
            Customers.Add(customer);
            return Created($"/api/customers/{customer.Id}", customer);
        }

        [HttpPost("login")]
        public ActionResult<Customer> Login([FromBody] LoginModel model)
        {
            Customer? customer = Customers.FirstOrDefault(
                c => c.Email == model.LogInEmail && c.Password == model.LogInPassword);
            return customer == null ? Unauthorized() : Ok(customer);
        }
    }
}
