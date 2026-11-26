using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<ShopDb>(options =>
    options.UseSqlServer(builder.Configuration.GetConnectionString("Main")));
var app = builder.Build();

app.MapPost("/api/items", async (Item item, ShopDb db) =>
{
    db.Items.Add(item);
    await db.SaveChangesAsync();
    return Results.Ok(item);
});

app.MapGet("/api/items", async (ShopDb db) => await db.Items.ToListAsync());

app.Run();

public class Item
{
    public int Id { get; set; }

    public string Name { get; set; } = "";
}

public class ShopDb : DbContext
{
    public ShopDb(DbContextOptions<ShopDb> options)
        : base(options)
    {
    }

    public DbSet<Item> Items => Set<Item>();
}
