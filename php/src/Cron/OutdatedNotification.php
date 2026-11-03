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

$isNextcloudImageOutdated = $dockerActionManager->isNextcloudImageOutdated();

if ($isNextcloudImageOutdated === true) {
    $dockerActionManager->sendNotification($nextcloudContainer, '¡APS Conecta Gestión está desactualizado!', 'Abra la interfaz de AIO de APS Conecta Gestión para actualizarlo. Si no desea hacerlo manualmente cada vez, active en esa interfaz la copia de seguridad diaria, que actualiza automáticamente todos los contenedores.');
}

