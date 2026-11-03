<?php
declare(strict_types=1);

// increase memory limit to 2GB
ini_set('memory_limit', '2048M');

use DI\Container;

require __DIR__ . '/../../vendor/autoload.php';

$container = \AIO\DependencyInjection::GetContainer();

/** @var \AIO\Docker\DockerActionManager $dockerActionManager */
$dockerActionManager = $container->get(\AIO\Docker\DockerActionManager::class);
/** @var \AIO\ContainerDefinitionFetcher $containerDefinitionFetcher */
$containerDefinitionFetcher = $container->get(\AIO\ContainerDefinitionFetcher::class);

$id = 'nextcloud-aio-nextcloud';
$nextcloudContainer = $containerDefinitionFetcher->GetContainerById($id);

$backupExitCode = $dockerActionManager->GetBackupcontainerExitCode();

if ($backupExitCode === 0) {
    if (getenv('SEND_SUCCESS_NOTIFICATIONS') === "0") {
        error_log("Daily backup successful! Only logging successful backup and not sending backup notification since that has been disabled! You can get further info by looking at the backup logs in the AIO interface.");
    } else {
        $dockerActionManager->sendNotification($nextcloudContainer, '¡Copia de seguridad diaria exitosa!', 'Puede ver más detalles en los registros de copias de seguridad de la interfaz de AIO de APS Conecta Gestión.');
    }
}

if ($backupExitCode > 0) {
    $dockerActionManager->sendNotification($nextcloudContainer, '¡Falló la copia de seguridad diaria!', 'Puede ver más detalles en los registros de copias de seguridad de la interfaz de AIO de APS Conecta Gestión.');
}
