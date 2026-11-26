using ScaffoldApi;

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

app.MapPost("/api/items", (Item item) =>
{
    using var context = new ShopContext();
    context.Items.Add(item);
    context.SaveChanges();
    return Results.Ok(item);
});

app.MapGet("/api/items", () =>
{
    using var context = new ShopContext();
    return context.Items.ToList();
});

app.Run();
