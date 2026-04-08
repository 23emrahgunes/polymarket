<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/lib/bootstrap.php';
require_once dirname(__DIR__) . '/lib/auth.php';
require_once dirname(__DIR__) . '/lib/data.php';

dashboard_require_auth();

$view = isset($_GET['view']) ? (string) $_GET['view'] : 'full';
dashboard_json_response(dashboard_build_payload($view));
