using System;
using System.Text;
using System.IO;
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
            var cloudBlockBlob = BlobAccess.GetBlobReference(containerName, blobName);

            var ms = new MemoryStream();
            await cloudBlockBlob.DownloadToStreamAsync(ms);            
            var bloContentsString = System.Convert.ToBase64String(ms.ToArray());
            return new OkObjectResult(bloContentsString);

        }
    }
}
