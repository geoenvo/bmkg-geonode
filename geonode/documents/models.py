import logging
import os
import uuid
import subprocess #^^

from django.db import models
from django.db.models import signals
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse
from django.contrib.contenttypes import generic
from django.contrib.staticfiles import finders
from django.utils.translation import ugettext_lazy as _

from geonode.layers.models import Layer
from geonode.base.models import ResourceBase, resourcebase_post_save, Link
from geonode.documents.enumerations import DOCUMENT_TYPE_MAP, DOCUMENT_MIMETYPE_MAP
from geonode.maps.signals import map_changed_signal
from geonode.maps.models import Map
from geonode.security.models import remove_object_permissions

from icraf_dr.models import Main #^^
from geonode.base.models import Region #^^

IMGTYPES = ['jpg', 'jpeg', 'tif', 'tiff', 'png', 'gif']

logger = logging.getLogger(__name__)


class Document(ResourceBase):

    """
    A document is any kind of information that can be attached to a map such as pdf, images, videos, xls...
    """
    
    def get_upload_path(instance, filename): #^^
        #^^ default upload path (replaced with custom upload path)
        upload_path = 'uploaded/documents/%s' % filename #^^
        
        if instance.main_id != None:
            main = Main.objects.get(pk=instance.main_id) #^^
            
            #^^ construct regions directory from
            #^^ region names in alphabetic order,
            #^^ replace whitespace with underscore,
            #^^ in lower case and separated by dash
            regions = [] #^^
            regions_id = main.regions.split(',') #^^
            for region_id in regions_id: #^^
                try: #^^
                    region = Region.objects.get(id=region_id) #^^
                    regions.append(region.name) #^^
                except: #^^
                    pass #^^
            regions.sort() #^^
            regions_dirname = '-'.join([region.replace(' ', '_').lower() for region in regions]) #^^
            
            # custom icraf_dr upload path
            upload_path = '{cat_alp}/{cov_alp}/{src_alp}/{year_num}/documents/{filename}'.format( #^^
                #cat_alp=main.category.cat_alp, #^^ replaced by TopicCategory identifier
                cat_alp=main.topic_category.identifier, #^^
                #cov_alp=main.coverage.cov_alp, #^^ replaced by regions_dirname
                cov_alp=regions_dirname, #^^
                src_alp=main.source.src_alp, #^^
                year_num=str(main.year.year_num), #^^
                filename=filename #^^
            )
        
        return upload_path
    
    def __init__(self, *args, **kwargs): #^^
        super(Document, self).__init__(*args, **kwargs) #^^
        self.main_id = None #^^
    
    # Relation to the resource model
    content_type = models.ForeignKey(ContentType, blank=True, null=True)
    object_id = models.PositiveIntegerField(blank=True, null=True)
    resource = generic.GenericForeignKey('content_type', 'object_id')

    #^^doc_file = models.FileField(upload_to='documents',
    doc_file = models.FileField(upload_to=get_upload_path, #^^
                                null=True,
                                blank=True,
                                verbose_name=_('File'))

    extension = models.CharField(max_length=128, blank=True, null=True)

    doc_type = models.CharField(max_length=128, blank=True, null=True)

    doc_url = models.URLField(
        blank=True,
        null=True,
        help_text=_('The URL of the document if it is external.'),
        verbose_name=_('URL'))

    def __unicode__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('document_detail', args=(self.id,))

    @property
    def name_long(self):
        if not self.title:
            return str(self.id)
        else:
            return '%s (%s)' % (self.title, self.id)

    def _render_thumbnail(self):
        from cStringIO import StringIO

        size = 200, 150

        try:
            from PIL import Image, ImageOps
        except ImportError, e:
            logger.error(
                '%s: Pillow not installed, cannot generate thumbnails.' %
                e)
            return None

        try:
            # if wand is installed, than use it for pdf thumbnailing
            from wand import image
        except:
            wand_available = False
        else:
            wand_available = True

        if wand_available and self.extension and self.extension.lower(
        ) == 'pdf' and self.doc_file:
            logger.debug(
                u'Generating a thumbnail for document: {0}'.format(
                    self.title))
            try:
                with image.Image(filename=self.doc_file.path) as img:
                    img.sample(*size)
                    return img.make_blob('png')
            except:
                logger.debug('Error generating the thumbnail with Wand, cascading to a default image...')
        
        #^^ start use unoconv to convert the first page to pdf and then create a thumbnail of the pdf
        MAX_CONVERT_MB = settings.MAX_DOCUMENT_SIZE
        if wand_available and self.extension.lower() in ['doc', 'docx', 'ppt', 'pptx', 'odt', 'odp'] and (os.path.getsize(self.doc_file.path) / 1024 / 1024) < MAX_CONVERT_MB:
            output_dir = 'tmpdoc/'
            output_path = settings.MEDIA_ROOT + '/' + output_dir
            output_format = 'pdf'
            output_file = os.path.basename(os.path.splitext(self.doc_file.path)[0]) + '.' + output_format
            output_file_path = output_path + output_file
            
            try:
                subprocess.check_call(['unoconv', '-f', output_format, '-e', 'PageRange=1', '-o', output_file_path, self.doc_file.path])
                if os.path.exists(output_file_path):
                    with image.Image(filename=output_file_path) as img:
                        img.sample(*size)
                        return img.make_blob('png')
            except subprocess.CalledProcessError as e:
                logger.debug('Error unoconv subprocess call')
        #^^ end
        
        # if we are still here, we use a default image thumb
        if self.extension and self.extension.lower() in IMGTYPES and self.doc_file:
            img = Image.open(self.doc_file.path)
            img = ImageOps.fit(img, size, Image.ANTIALIAS)
        else:
            filename = finders.find('documents/{0}-placeholder.png'.format(self.extension), False) or \
                finders.find('documents/generic-placeholder.png', False)

            if not filename:
                return None

            img = Image.open(filename)

        imgfile = StringIO()
        img.save(imgfile, format='PNG')
        return imgfile.getvalue()

    @property
    def class_name(self):
        return self.__class__.__name__

    class Meta(ResourceBase.Meta):
        pass


def get_related_documents(resource):
    if isinstance(resource, Layer) or isinstance(resource, Map):
        ct = ContentType.objects.get_for_model(resource)
        return Document.objects.filter(content_type=ct, object_id=resource.pk)
    else:
        return None


def pre_save_document(instance, sender, **kwargs):
    base_name, extension, doc_type = None, None, None

    if instance.doc_file:
        base_name, extension = os.path.splitext(instance.doc_file.name)
        instance.extension = extension[1:]
        doc_type_map = DOCUMENT_TYPE_MAP
        doc_type_map.update(getattr(settings, 'DOCUMENT_TYPE_MAP', {}))
        if doc_type_map is None:
            doc_type = 'other'
        else:
            doc_type = doc_type_map.get(instance.extension, 'other')
        instance.doc_type = doc_type

    elif instance.doc_url:
        if len(instance.doc_url) > 4 and instance.doc_url[-4] == '.':
            instance.extension = instance.doc_url[-3:]

    if not instance.uuid:
        instance.uuid = str(uuid.uuid1())
    instance.csw_type = 'document'

    if instance.abstract == '' or instance.abstract is None:
        instance.abstract = 'No abstract provided'

    if instance.title == '' or instance.title is None:
        instance.title = instance.doc_file.name

    if instance.resource:
        instance.csw_wkt_geometry = instance.resource.geographic_bounding_box.split(
            ';')[-1]
        instance.bbox_x0 = instance.resource.bbox_x0
        instance.bbox_x1 = instance.resource.bbox_x1
        instance.bbox_y0 = instance.resource.bbox_y0
        instance.bbox_y1 = instance.resource.bbox_y1
    else:
        instance.bbox_x0 = -180
        instance.bbox_x1 = 180
        instance.bbox_y0 = -90
        instance.bbox_y1 = 90


def post_save_document(instance, *args, **kwargs):

    name = None
    ext = instance.extension
    mime_type_map = DOCUMENT_MIMETYPE_MAP
    mime_type_map.update(getattr(settings, 'DOCUMENT_MIMETYPE_MAP', {}))
    mime = mime_type_map.get(ext, 'text/plain')
    url = None

    if instance.doc_file:
        name = "Hosted Document"
        url = '%s%s' % (
            settings.SITEURL[:-1],
            reverse('document_download', args=(instance.id,)))
    elif instance.doc_url:
        name = "External Document"
        url = instance.doc_url

    if name and url:
        Link.objects.get_or_create(
            resource=instance.resourcebase_ptr,
            url=url,
            defaults=dict(
                extension=ext,
                name=name,
                mime=mime,
                url=url,
                link_type='data',))


def create_thumbnail(sender, instance, created, **kwargs):
    from geonode.tasks.update import create_document_thumbnail

    create_document_thumbnail.delay(object_id=instance.id)


def update_documents_extent(sender, **kwargs):
    model = 'map' if isinstance(sender, Map) else 'layer'
    ctype = ContentType.objects.get(model=model)
    for document in Document.objects.filter(content_type=ctype, object_id=sender.id):
        document.save()


def pre_delete_document(instance, sender, **kwargs):
    remove_object_permissions(instance.get_self_resource())


signals.pre_save.connect(pre_save_document, sender=Document)
signals.post_save.connect(create_thumbnail, sender=Document)
signals.post_save.connect(post_save_document, sender=Document)
signals.post_save.connect(resourcebase_post_save, sender=Document)
signals.pre_delete.connect(pre_delete_document, sender=Document)
map_changed_signal.connect(update_documents_extent)
