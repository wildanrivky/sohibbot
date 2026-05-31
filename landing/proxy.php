<?php
// Proxy untuk order creation - API key disimpan server-side, tidak expose ke browser
define('ERP_ENDPOINT', 'https://admin.sohibbot.com/api/public/orders/create');
define('API_KEY', '87936e9d5333366d3c9ce99de483421c24e78decbefd7b57');
define('RATE_LIMIT', 10);

header('Content-Type: application/json');

$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
$allowed = ['https://sohibbot.com', 'https://www.sohibbot.com', 'http://localhost'];
if (in_array($origin, $allowed, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
}
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    exit(json_encode(['error' => 'Method not allowed']));
}

// Rate limit per IP per menit
$ip   = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
$file = sys_get_temp_dir() . '/sohibbot_rl_' . md5($ip) . '.json';
$now  = time();
$rl   = file_exists($file) ? json_decode(file_get_contents($file), true) : ['count' => 0, 'reset' => $now + 60];
if ($now > $rl['reset']) {
    $rl = ['count' => 0, 'reset' => $now + 60];
}
if ($rl['count'] >= RATE_LIMIT) {
    http_response_code(429);
    exit(json_encode(['error' => 'Too many requests, coba lagi dalam 1 menit']));
}
$rl['count']++;
file_put_contents($file, json_encode($rl), LOCK_EX);

// Validasi input
$body  = file_get_contents('php://input');
$order = json_decode($body, true);
if (!is_array($order) || empty($order['nama']) || empty($order['email']) || empty($order['wa']) || empty($order['paket'])) {
    http_response_code(400);
    exit(json_encode(['error' => 'Field tidak lengkap']));
}
if (!filter_var($order['email'], FILTER_VALIDATE_EMAIL)) {
    http_response_code(400);
    exit(json_encode(['error' => 'Email tidak valid']));
}

// Forward ke ERP - API key ditambahkan di sini (server-side)
$ch = curl_init(ERP_ENDPOINT);
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($order));
curl_setopt($ch, CURLOPT_HTTPHEADER, [
    'Content-Type: application/json',
    'X-API-Key: ' . API_KEY,
]);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 10);
curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);

$response   = curl_exec($ch);
$httpStatus = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$curlError  = curl_error($ch);
curl_close($ch);

if ($curlError) {
    http_response_code(502);
    exit(json_encode(['error' => 'Gagal menghubungi server']));
}

http_response_code($httpStatus ?: 502);
echo $response ?: json_encode(['error' => 'Empty response dari server']);
