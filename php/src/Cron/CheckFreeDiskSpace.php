<?php
declare(strict_types=1);

// increase memory limit to 2GB
ini_set('memory_limit', '2048M');

use DI\Container;
use AIO\Data\DataConst;

require __DIR__ . '/../../vendor/autoload.php';

$container = \AIO\DependencyInjection::GetContainer();

/** @var \AIO\Docker\DockerActionManager $dockerActionManager */
$dockerActionManager = $container->get(\AIO\Docker\DockerActionManager::class);
/** @var \AIO\ContainerDefinitionFetcher $containerDefinitionFetcher */
$containerDefinitionFetcher = $container->get(\AIO\ContainerDefinitionFetcher::class);

$id = 'nextcloud-aio-nextcloud';
$nextcloudContainer = $containerDefinitionFetcher->GetContainerById($id);

$df = disk_free_space(DataConst::GetDataDirectory());
if ($df !== false && (int)$df < 1024 * 1024 * 1024 * 5) {
    error_log("The drive that hosts the mastercontainer volume has less than 5 GB free space. Container updates and backups might not succeed due to that!");
    $dockerActionManager->sendNotification($nextcloudContainer, '¡Queda poco espacio en disco!', 'La unidad que aloja el volumen del mastercontainer de APS Conecta Gestión tiene menos de 5 GB libres. Por eso, las actualizaciones de los contenedores y las copias de seguridad podrían fallar.');
}
