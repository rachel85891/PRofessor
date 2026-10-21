using Microsoft.AspNetCore.Mvc;
using SampleWebApi.Models;

namespace SampleWebApi.Controllers;

[ApiController]
[Route("api/[controller]")]
public class ProductsController : ControllerBase
{
    private static readonly List<Product> products = new List<Product>
    {
        new Product { Id = 1, Name = "Pen", Price = 3.5m },
        new Product { Id = 2, Name = "Notebook", Price = 12m }
    };

    [HttpGet]
    public IActionResult GetAll()
    {
        return Ok(products);
    }

    [HttpGet("{id}")]
    public IActionResult GetById(int id)
    {
        Product? product = products.FirstOrDefault(p => p.Id == id);
        if (product == null)
        {
            return NotFound();
        }
        return Ok(product);
    }

    [HttpPost]
    public IActionResult Create(Product product)
    {
        product.Id = products.Count == 0 ? 1 : products.Max(p => p.Id) + 1;
        products.Add(product);
        return CreatedAtAction(nameof(GetById), new { id = product.Id }, product);
    }
}
