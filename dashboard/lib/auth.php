<?php
declare(strict_types=1);

require_once __DIR__ . '/bootstrap.php';

function dashboard_extract_basic_auth(): array
{
    $user = $_SERVER['PHP_AUTH_USER'] ?? null;
    $password = $_SERVER['PHP_AUTH_PW'] ?? null;

    if ($user !== null && $password !== null) {
        return [$user, $password];
    }

    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['Authorization'] ?? '';
    if (!str_starts_with($header, 'Basic ')) {
        return [null, null];
    }

    $decoded = base64_decode(substr($header, 6), true);
    if ($decoded === false || !str_contains($decoded, ':')) {
        return [null, null];
    }

    [$parsedUser, $parsedPassword] = explode(':', $decoded, 2);
    return [$parsedUser, $parsedPassword];
}

function dashboard_unauthorized(string $message = 'Unauthorized'): never
{
    header("WWW-Authenticate: Basic realm=\"Ghost Trader Dashboard\"");
    http_response_code(401);
    header('Content-Type: text/plain; charset=utf-8');
    echo $message;
    exit;
}

function dashboard_require_auth(): void
{
    if (dashboard_bool_env('DASHBOARD_DISABLE_AUTH', false)) {
        return;
    }

    $expectedUser = dashboard_env('DASHBOARD_USER', 'admin') ?? 'admin';
    $passwordHash = dashboard_env('DASHBOARD_PASSWORD_HASH', '') ?? '';

    if ($passwordHash === '') {
        dashboard_unauthorized('Dashboard password hash is missing.');
    }

    [$user, $password] = dashboard_extract_basic_auth();
    if ($user === null || $password === null) {
        dashboard_unauthorized();
    }

    if (!hash_equals($expectedUser, $user) || !password_verify($password, $passwordHash)) {
        dashboard_unauthorized();
    }
}
