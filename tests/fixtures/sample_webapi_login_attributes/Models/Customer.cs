using System.ComponentModel.DataAnnotations;

namespace LoginAttributesApi.Models
{
    public class Customer
    {
        public int Id { get; set; }
        public string Email { get; set; } = "";
        public string Password { get; set; } = "";
    }

    // Field names that say what they are for, with validation attributes.
    public class LoginModel
    {
        [Required]
        [EmailAddress]
        public string LogInEmail { get; set; } = "";

        [Required]
        public string LogInPassword { get; set; } = "";
    }
}
