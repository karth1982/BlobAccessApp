using System;
using System.Text;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Extensions.Http;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Azure.Storage;
using Microsoft.Azure.Storage.Blob;
using Newtonsoft.Json;
using BlobAccessApp.Common;

namespace BlobAccessApp.Functions
{
    public static class DownloadBlob
    {

        [FunctionName("DownloadBlob")]
        public static async Task<IActionResult> Run(
            [HttpTrigger(AuthorizationLevel.Anonymous, "get",
            Route = "container/{containerName}")] HttpRequest req,
            string containerName,
            ILogger log)
        {            

            var blobName = req.Query["blob"][0];
            //log.LogInformation($"Blob Name:{blobName}");

            var blobSasUri = BlobAccess.CreateDownloadSas(containerName, blobName);

            if (BlobAccess.blobHttpClient == null)
                BlobAccess.blobHttpClient = new HttpClient();

            var blobStream = await BlobAccess.blobHttpClient.GetStreamAsync(blobSasUri);
            return new OkObjectResult(blobStream);

        }
    }
}
