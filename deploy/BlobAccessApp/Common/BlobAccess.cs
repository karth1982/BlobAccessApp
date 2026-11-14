using System;
using System.IO;
using System.Threading.Tasks;
using System.Net.Http;
using Microsoft.Azure.Storage;
using Microsoft.Azure.Storage.Blob;

namespace BlobAccessApp.Common
{
    public class BlobAccess
    {

        public static HttpClient blobHttpClient = null;
        public static CloudBlockBlob GetBlobReference(string containerNameString,
                                                        string blobNameString)
        {

            CloudStorageAccount cloudStorageAccount = null;
            var connectionString = Environment.GetEnvironmentVariable("AzureWebJobsStorage");
            var couldParse = CloudStorageAccount.TryParse(connectionString, out cloudStorageAccount);
            if (couldParse == false)
                return null;

            var cloudBlobClient = cloudStorageAccount.CreateCloudBlobClient();
            var blobContainerReference = cloudBlobClient.GetContainerReference(containerNameString);
            var blobReference = blobContainerReference.GetBlockBlobReference(blobNameString);
            return blobReference;

        }

        public static string CreateDownloadSas(string containerNameString,
                                               string blobNameString)
        {

            var blobReference = GetBlobReference(containerNameString, blobNameString);
            var blobUri = blobReference.Uri;

            var sasPolicy = new SharedAccessBlobPolicy()
            {

                Permissions = SharedAccessBlobPermissions.Read,
                SharedAccessExpiryTime = DateTime.UtcNow.AddMinutes(2)

            };

            var blobSas = blobReference.GetSharedAccessSignature(sasPolicy);
            blobSas = blobUri + blobSas;
            return blobSas;

        }

        public static string CreateUploadSas(string containerNameString,
                                             string blobNameString)
        {

            var blobReference = GetBlobReference(containerNameString, blobNameString);
            var blobUri = blobReference.Uri;

            var sasPolicy = new SharedAccessBlobPolicy()
            {

                Permissions = SharedAccessBlobPermissions.Write |
                              SharedAccessBlobPermissions.Add |
                              SharedAccessBlobPermissions.Create,
                SharedAccessExpiryTime = DateTime.UtcNow.AddMinutes(5)

            };

            var blobSas = blobReference.GetSharedAccessSignature(sasPolicy);
            blobSas = blobUri + blobSas;
            return blobSas;

        }


    }
}
