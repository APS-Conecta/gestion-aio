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

$isMastercontainerUpdateAvailable = $dockerActionManager->IsMastercontainerUpdateAvailable();
$isAnyUpdateAvailable = $dockerActionManager->isAnyUpdateAvailable();

if ($isMastercontainerUpdateAvailable === true) {
    $dockerActionManager->sendNotification($nextcloudContainer, '¡Hay una actualización del mastercontainer de APS Conecta Gestión!', 'Abra la interfaz de AIO de APS Conecta Gestión para actualizarlo. Si no desea hacerlo manualmente cada vez, active en esa interfaz la copia de seguridad diaria, que también actualiza automáticamente el mastercontainer.');
}

if ($isAnyUpdateAvailable === true) {
    $dockerActionManager->sendNotification($nextcloudContainer, '¡Hay actualizaciones de contenedores de APS Conecta Gestión!', 'Abra la interfaz de AIO de APS Conecta Gestión para actualizarlos. Si no desea hacerlo manualmente cada vez, active en esa interfaz la copia de seguridad diaria, que también actualiza automáticamente sus contenedores y sus aplicaciones.');
}
