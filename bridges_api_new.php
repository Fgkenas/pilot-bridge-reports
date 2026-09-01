<?php
/**
 * GET /get_bridges.php
 * Fetches bridge info from 3 external APIs and joins with data_bridges_api table.
 * Results are grouped by region, then by bridge, then by site_id.
 *
 * Optional params:
 *   ?code_name=ΔΕ_012_ΓΟΥ  → filter by specific bridge
 *   ?region_code=ΔΕ        → filter by region
 *   (both can be combined)
 */

header('Content-Type: application/json; charset=utf-8');

// ── DB credentials (from environment, see secrets/.env.example) ──────────────
$db_host = getenv('SQL_HOST') ?: 'gpark.gr';
$db_user = getenv('SQL_USERNAME') ?: '';
$db_pass = getenv('SQL_PASSWORD') ?: '';
$db_name = getenv('SQL_DATABASE') ?: 'kalamata_gpark';

// ── External APIs (keys from environment) ─────────────────────────────────────
$apis = [
    'stats'  => 'https://stats.lmsg.gr/api/bridges/get_all_bridges.php?key=' . getenv('BRIDGES_API_KEY_STATS'),
    'statsc' => 'https://statsc.lmsg.gr/api/bridges/get_all_bridges.php?key=' . getenv('BRIDGES_API_KEY_STATSC'),
    'statsb' => 'https://statsb.lmsg.gr/api/bridges/get_all_bridges.php?key=' . getenv('BRIDGES_API_KEY_STATSB'),
];

// ── Image base URL per API source ─────────────────────────────────────────────
$image_bases = [
    'stats'  => 'https://stats.lmsg.gr/regions/bridges/images',
    'statsc' => 'https://statsc.lmsg.gr/regions/bridges/images',
    'statsb' => 'https://statsb.lmsg.gr/regions/bridges/images',
];

// ── Helper: fetch one API URL ─────────────────────────────────────────────────
function fetch_api(string $url): array {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 10,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_HTTPHEADER     => ['Accept: application/json'],
    ]);
    $body = curl_exec($ch);
    $err  = curl_error($ch);
    curl_close($ch);

    if ($err || $body === false) return [];
    $data = json_decode($body, true);
    return is_array($data) ? $data : [];
}

// ── Helper: build full image URLs from a bridge row ───────────────────────────
function build_image_urls(array $bridge, array $image_bases): array {
    $source = $bridge['_source'] ?? null;
    $id     = $bridge['id']      ?? null;
    $image  = $bridge['image']   ?? null;

    if (!$source || $id === null || !$image || !isset($image_bases[$source])) {
        return [];
    }

    $filenames = json_decode($image, true);
    if (!is_array($filenames)) return [];

    $base = $image_bases[$source];
    $urls = [];
    foreach ($filenames as $filename) {
        if (is_string($filename) && $filename !== '') {
            $urls[] = "{$base}/{$id}/{$filename}";
        }
    }
    return $urls;
}

// ── Helper: recognize text  ────────────────────────────
function parse_txt_content(string $txt_file): array
{
    $result = [
        'caption' => null,
        'report_text' => null,
    ];

    if (!file_exists($txt_file)) {
        return $result;
    }

    $content = trim(file_get_contents($txt_file));

    if ($content === '') {
        return $result;
    }

    $lines = preg_split('/\R/u', $content);

    $result['caption'] = trim(array_shift($lines));
    $result['report_text'] = trim(implode("\n", $lines));

    return $result;
}


// ── Helper: strip redundant fields from sensor row ────────────────────────────
function clean_sensor(array $row): array {
    // Remove fields already present at parent level
    unset(
        $row['id'],
        $row['site_id'],
        $row['Bridge_Name'],
        $row['region_code'],
        $row['region_name'],
        $row['region_name_el']
    );
    return $row;
}

// ── Helper: parse description text for EGSA coordinates and z_height ──────────
/**
 * Parses a description string and extracts:
 *  - EGSA_coordinates: a pair (x, y) following the phrase "συντεταγμένες ΕΓΣΑ"
 *      e.g. "συντεταγμένες ΕΓΣΑ είναι (667582.39, 4335533.33)"
 *      → ['x' => 667582.39, 'y' => 4335533.33]
 *  - z_height: a number following "υψόμετρο" or "ύψος"
 *      e.g. "υψόμετρο 125.4 μ" → 125.4
 *
 * Returns: ['EGSA_coordinates' => array|null, 'z_height' => float|null]
 */
function parse_description($text): array {
    $result = [
        'EGSA_coordinates' => null,
        'z_height'         => null,
    ];

    if (!is_string($text) || $text === '') {
        return $result;
    }


    // Normalize unicode (combining accents) so case-insensitive Greek match works
    // u flag handles UTF-8 properly
    //
    // EGSA coordinates pattern:
    // Look for "συντεταγμένες ΕΓΣΑ" then any chars (e.g. "είναι"), then "(x, y)"
    // Accepts both Greek "ΕΓΣΑ" and uppercase/lowercase variations.
    // Numbers can be int or decimal (dot), optionally negative.
    $egsa_pattern = '/συντεταγμ[έεΈΕ]ν[εεΕΕ]ς\s*ΕΓΣΑ[^()\d\-]*\(?\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*\)?/iu';

    if (preg_match($egsa_pattern, $text, $m)) {
        $result['EGSA_coordinates'] = [
            'x' => (float) $m[1],
            'y' => (float) $m[2],
        ];
    }

    // z_height pattern:
    // Look for "υψόμετρο" or "ύψος" followed by a number.
    // Allow optional ":", "=", "είναι", "στα" etc. between keyword and number.
    // Number can use either DOT (.) or COMMA (,) as decimal separator
    // (Greek convention writes "75,3" instead of "75.3").
    // Greek "ό" / "ο" / "ύ" / "υ" handled with character classes; case-insensitive via /iu.
    $z_pattern = '/(?:υψ[όοΌΟ]μετρ[οόΟΌ]|[ύυΎΥ]ψ[οόΟΌ]ς)[^\d\-]{0,30}(-?\d+(?:[.,]\d+)?)/iu';

    if (preg_match($z_pattern, $text, $m)) {
        // Normalize Greek decimal comma to dot before casting to float
        $num = str_replace(',', '.', $m[1]);
        $result['z_height'] = (float) $num;
    }

    return $result;
}


function normalize_bridge_name(string $name): string
{
    $map = [
        'Κ' => 'K',
        'Μ' => 'M',
        'Α' => 'A',
        'Β' => 'B',
        'Ε' => 'E',
        'Η' => 'H',
        'Ι' => 'I',
        'Ν' => 'N',
        'Ο' => 'O',
        'Ρ' => 'P',
        'Τ' => 'T',
        'Χ' => 'X',
        'Υ' => 'Y',
    ];

    return strtoupper(strtr(trim($name), $map));
}

    // ── Load bridge locations from CSV files ─────────────────────────────────────

function load_bridge_locations(string $dir): array
{
    $locations = [];

    if (!is_dir($dir)) {
        return $locations;
    }

    foreach (glob($dir . '/*.csv') as $csvFile) {

        if (($handle = fopen($csvFile, 'r')) === false) {
            continue;
        }

        // Header
        $header = fgetcsv($handle, 0, ';');

        if (!$header) {
            fclose($handle);
            continue;
        }

        $header = array_map(function($v) {
            return trim(preg_replace('/^\xEF\xBB\xBF/', '', $v));
        }, $header);

        $codeIndex = array_search('code_name', $header);
        $locIndex  = array_search('location', $header);

        if ($codeIndex === false || $locIndex === false) {
            fclose($handle);
            continue;
        }

        while (($row = fgetcsv($handle, 0, ';')) !== false) {

            $code_name = trim($row[$codeIndex] ?? '');
            $location  = trim($row[$locIndex] ?? '');

            if ($code_name === '') {
                continue;
            }

            $locations[$code_name] = $location;
        }

        fclose($handle);
    }

    return $locations;
}

// ── Optional filters ──────────────────────────────────────────────────────────
$code_name   = isset($_GET['code_name'])   ? trim($_GET['code_name'])   : null;
$region_code = isset($_GET['region_code']) ? trim($_GET['region_code']) : null;

// ── Connect to DB ─────────────────────────────────────────────────────────────
try {
    $pdo = new PDO(
        "mysql:host={$db_host};dbname={$db_name};charset=utf8mb4",
        $db_user,
        $db_pass,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(['error' => 'DB connection failed: ' . $e->getMessage()]);
    exit;
}

// ── Build query with optional filters ─────────────────────────────────────────
$sql = "
    SELECT
        d.*,
        r.code                  AS region_code,
        r.name                  AS region_name,
        r.name_el               AS region_name_el,
        r.region_name_el_report AS region_name_el_report
    FROM data_bridges_api d
    LEFT JOIN region r ON r.code = d.region_code
    WHERE 1=1
";
$params = [];

if ($code_name) {
    $sql .= " AND d.Bridge_Name = :code_name";
    $params[':code_name'] = $code_name;
}
if ($region_code) {
    $sql .= " AND d.region_code = :region_code";
    $params[':region_code'] = $region_code;
}

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$sensor_rows = $stmt->fetchAll(PDO::FETCH_ASSOC);

// ── Group: region_code → Bridge_Name → site_id → [ clean sensors ] ────────────
$regions       = [];       // region_code => region meta
$by_region     = [];       // region_code => Bridge_Name => site_id => [sensors]
$bridge_region = [];       // Bridge_Name => region_code  (for matching APIs later)

foreach ($sensor_rows as $row) {
    $rcode   = $row['region_code'] ?? '__UNKNOWN__';
    $bridge  = $row['Bridge_Name'];
    $site_id = $row['site_id'];

    if (!isset($regions[$rcode])) {
        $regions[$rcode] = [
            'region_code'           => $row['region_code'] ?? null,
            'region_name'           => $row['region_name'] ?? null,
            'region_name_el'        => $row['region_name_el'] ?? null,
            'region_name_el_report' => $row['region_name_el_report'] ?? null,
        ];
    }

    $by_region[$rcode][$bridge][$site_id][] = clean_sensor($row);
    $bridge_region[$bridge] = $rcode;
}

$locations_dir = __DIR__ . '/locations';
$bridge_locations = load_bridge_locations($locations_dir);

// ── Fetch & merge bridges from all APIs ───────────────────────────────────────
$api_bridges = [];
foreach ($apis as $source => $url) {

    $rows = fetch_api($url);

    foreach ($rows as $row) {

        $key = $row['code_name'] ?? null;

        if (!$key) {
            continue;
        }

        $normalized_key = normalize_bridge_name($key);

        if (!isset($api_bridges[$normalized_key])) {

            $row['_source'] = $source;

            $row['image_urls'] = build_image_urls(
                $row + ['_source' => $source],
                $image_bases
            );

            $api_bridges[$normalized_key] = $row;
        }
    }
}


// ── Helper: format one bridge ─────────────────────────────────────────────────
function format_bridge(
    string $key,
    ?array $bridge_info,
    array $sensors_grouped,
    array $photo_text_by_bridge = [],
    array $bridge_locations = []
): array {

    $sites = [];
    foreach ($sensors_grouped as $site_id => $sensors) {
        $sites[] = [
            'site_id' => $site_id,
            'sensors' => $sensors,
        ];
    }

    // Parse description (if available) to extract EGSA_coordinates and z_height
    $description = null;

    if (is_array($bridge_info)) {
        $description = $bridge_info['description']
            ?? $bridge_info['Description']
            ?? $bridge_info['desc']
            ?? null;
    }

    $parsed = parse_description($description);


    // Build output with EGSA_coordinates and z_height directly under code_name

return [
    'code_name'        => $key,
    'location'         => $bridge_locations[$key] ?? null,
    'EGSA_coordinates' => $parsed['EGSA_coordinates'],
    'z_height'         => $parsed['z_height'],
    'bridge_info'      => $bridge_info,

    'influence_lines' => $photo_text_by_bridge[$key] ?? new stdClass(),

    'sites' => $sites,
];


}

// ── Photo/Text pair loader ────────────────────────────────────────────────────

$photo_text_base_url = 'http://lms4.globiled.com/Bridges_api/photos_and_text';
$photo_text_dir      = __DIR__ . '/photos_and_text';

/**
 * Extract code_name:
 * Example:
 * IL_ΚΡ_009_ΜΧΡ_SG-02
 * -> IL_ΚΡ_009_ΜΧΡ
 */
function extract_code_name(string $filename): string {

    $parts = explode('_', $filename);

    if (count($parts) < 5) {
        return $filename;
    }

    return implode('_', array_slice($parts, 1, 3));
}

function extract_region_code(string $filename): ?string {

    $parts = explode('_', $filename);

    return $parts[1] ?? null;
}

/**
 * Load photo/text pairs
 */
function load_photo_text_pairs(string $dir, string $base_url): array {

    if (!is_dir($dir)) {
        return [];
    }

    $files = scandir($dir);

    $pairs = [];

    // First pass: collect png files
    foreach ($files as $file) {

        if (pathinfo($file, PATHINFO_EXTENSION) !== 'png') {
            continue;
        }

        $basename = pathinfo($file, PATHINFO_FILENAME);

        $txt_file = $dir . '/' . $basename . '.txt';

        $txt_data = parse_txt_content($txt_file);

        $pairs[] = [
			'filename'    => $basename,
			'code_name'   => extract_code_name($basename),
			'region_code' => extract_region_code($basename),
			'image_url' => $base_url . '/' . str_replace('%2F', '/', rawurlencode($file)),
			'caption'     => $txt_data['caption'],
			'report_text' => $txt_data['report_text'],
		];
    }

    return $pairs;
}

$photo_text_pairs = load_photo_text_pairs(
    $photo_text_dir,
    $photo_text_base_url
);

// Group by code_name
$photo_text_by_bridge = [];

foreach ($photo_text_pairs as $pair) {

    $code_name = $pair['code_name'];

    if (!isset($photo_text_by_bridge[$code_name])) {
        $photo_text_by_bridge[$code_name] = [];
    }

    // remove code_name from child object
    $clean_pair = $pair;
    unset($clean_pair['code_name']);

    $index = count($photo_text_by_bridge[$code_name]) + 1;

	$photo_text_by_bridge[$code_name]["InfluenceLine{$index}"] = [
		'Plot'        => $clean_pair['image_url'],
		'PlotCaption' => $clean_pair['caption'],
		'ReportText'  => $clean_pair['report_text'],
	];
}


// ── Build region-grouped result ───────────────────────────────────────────────
$result_regions = [];

// Collect all bridge keys per region (from DB + from APIs that match by region filter)
foreach ($by_region as $rcode => $bridges) {
    $bridges_out = [];
    foreach ($bridges as $bridge_key => $sites_grouped) {

$normalized_bridge_key = normalize_bridge_name($bridge_key);

$bridges_out[] = format_bridge(
    $bridge_key,
    $api_bridges[$normalized_bridge_key] ?? null,
    $sites_grouped,
    $photo_text_by_bridge,
    $bridge_locations
);

    }

    $result_regions[] = [
        'region_code'            => $regions[$rcode]['region_code'],
        'region_name'            => $regions[$rcode]['region_name'],
        'region_name_el'         => $regions[$rcode]['region_name_el'],
        'region_name_el_report'  => $regions[$rcode]['region_name_el_report'] ?? null,
        'bridge_count'           => count($bridges_out),
        'bridges'                => $bridges_out,
    ];
}

// ── Output ────────────────────────────────────────────────────────────────────
if (empty($result_regions)) {
    http_response_code(404);
    $msg = 'No data found';
    if ($code_name)   $msg .= " for code_name: {$code_name}";
    if ($region_code) $msg .= " in region: {$region_code}";
    echo json_encode(['error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

// Total bridge count across all regions
$total_bridges = 0;
foreach ($result_regions as $r) {
    $total_bridges += $r['bridge_count'];
}


echo json_encode([
    'region_count'  => count($result_regions),
    'total_bridges' => $total_bridges,

    'filters' => [
        'code_name'   => null,
        'region_code' => null,
    ],

    'regions' => $result_regions,

], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);