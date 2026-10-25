using Microsoft.AspNetCore.Mvc;

namespace Net8Api.Controllers
{
    public class Item
    {
        public int Id { get; set; }
        public string Title { get; set; } = "";
    }

    [Route("api/[controller]")]
    [ApiController]
    public class ItemsController : ControllerBase
    {
        private static readonly List<Item> Items = new List<Item>
        {
            new Item { Id = 1, Title = "First" },
        };

        [HttpGet]
        public ActionResult<IEnumerable<Item>> GetAll()
        {
            return Ok(Items);
        }

        [HttpPost]
        public ActionResult<Item> Add([FromBody] Item item)
        {
            item.Id = Items.Max(i => i.Id) + 1;
            Items.Add(item);
            return Created($"/api/items/{item.Id}", item);
        }
    }
}
