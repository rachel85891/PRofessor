from hw_checker.domain import DeclaredRoute, HttpMethod, RouteTable
from hw_checker.domain.routing import declares_route


def route(method: HttpMethod | None, template: str) -> DeclaredRoute:
    return DeclaredRoute(
        method=method,
        template=template,
        controller="UsersController",
        action="Action",
        file_path="Api/Controllers/UsersController.cs",
        line=1,
    )


TABLE = RouteTable(routes=(route(HttpMethod.GET, "api/Users/{id}"), route(None, "api/any")))


def test_a_route_is_declared_for_its_method_ignoring_case() -> None:
    assert declares_route(TABLE, HttpMethod.GET, "/API/users/5")
    assert declares_route(TABLE, HttpMethod.GET, "/api/users/{…}")
    assert not declares_route(TABLE, HttpMethod.DELETE, "/api/users/5")


def test_a_route_without_a_method_answers_every_method() -> None:
    assert declares_route(TABLE, HttpMethod.DELETE, "/api/any")
