using System;
using System.Text;
using System.IO;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Azure.WebJobs;
using Microsoft.Azure.WebJobs.Extensions.Http;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Azure.Storage.Blob;
using Newtonsoft.Json;
using BlobAccessApp.Common;

namespace BlobAccessApp.Functions
{
    public static class UploadBlob
    {
        [FunctionName("UploadBlob")]
        public static async Task<IActionResult> Run(
            [HttpTrigger(AuthorizationLevel.Anonymous, "post",
            Route = "container/{containerName}")] HttpRequest req,
            string containerName,
            ILogger log)
        {            

            var blobName = req.Query["blob"][0];
            var blobString = await new StreamReader(req.Body).ReadToEndAsync();
            var blobBytesArray = Encoding.UTF8.GetBytes(blobString);            

            var blobSasUri = BlobAccess.CreateUploadSas(containerName, blobName);
            var cloudBlockBlob = new CloudBlockBlob(new Uri(blobSasUri));
            await cloudBlockBlob.UploadFromByteArrayAsync(blobBytesArray, 0,
                                                          blobBytesArray.Length);

            return new OkObjectResult("OK");

        }
    }
}
