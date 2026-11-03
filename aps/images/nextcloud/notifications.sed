# SPDX-FileCopyrightText: 2026 APS Conecta
# SPDX-License-Identifier: AGPL-3.0-or-later
# The notifications upstream's /entrypoint.sh posts through /notify.sh, in Spanish and naming «APS Conecta Gestión».
s|"Nextcloud update to \$image_version failed!" "Please restore from backup."|"¡Falló la actualización de APS Conecta Gestión a $image_version!" "Restáurelo desde una copia de seguridad."|
s|"Nextcloud update to \$image_version successful!" "You may inspect the Nextcloud container logs for more information."|"¡APS Conecta Gestión se actualizó a $image_version!" "Puede revisar los registros del servidor de aplicaciones para obtener más información."|
s|CUSTOM_HINT="The apporder app was deprecated. A possible replacement is the side_menu app, aka 'Custom menu'."|CUSTOM_HINT="La aplicación apporder está obsoleta. Un posible reemplazo es la aplicación side_menu («Custom menu»)."|
s|CUSTOM_HINT="Most likely, it is not compatible with the new Nextcloud version."|CUSTOM_HINT="Lo más probable es que no sea compatible con la nueva versión de APS Conecta Gestión."|
s|"Could not re-enable the \$app app after the Nextcloud update!" "\$CUSTOM_HINT Feel free to review the Nextcloud update logs and force-enable the app again if you wish."|"¡No se pudo reactivar la aplicación $app tras la actualización de APS Conecta Gestión!" "$CUSTOM_HINT Si lo desea, revise los registros de la actualización y fuerce la activación de la aplicación."|
s|"Your apps just got updated!"|"¡Se actualizaron las aplicaciones de APS Conecta Gestión!"|
s|"Onlyoffice did not start in time!" "Skipping initialization and disabling onlyoffice app."|"¡Onlyoffice no se inició a tiempo!" "Se omitió su configuración en APS Conecta Gestión y se desactivó la aplicación onlyoffice."|
s|"EuroOffice did not start in time!" "Skipping initialization and disabling eurooffice app."|"¡EuroOffice no se inició a tiempo!" "Se omitió su configuración en APS Conecta Gestión y se desactivó la aplicación eurooffice."|
s|"ClamAV did not start in time!" "Skipping initialization and disabling files_antivirus app."|"¡ClamAV no se inició a tiempo!" "Se omitió su configuración en APS Conecta Gestión y se desactivó la aplicación files_antivirus."|
