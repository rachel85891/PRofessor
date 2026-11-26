using Microsoft.EntityFrameworkCore;
using SplitData;
using SplitModels;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddDbContext<ShopDb>(options =>
    options.UseSqlServer(builder.Configuration.GetConnectionString("Main")));
builder.Services.AddScoped<ShopRepository>();
var app = builder.Build();

app.MapPost("/api/items", (Item item, ShopRepository repository) => repository.Add(item));
app.MapGet("/api/items", (ShopRepository repository) => repository.All());

app.Run();
