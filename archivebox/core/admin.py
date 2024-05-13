__package__ = 'archivebox.core'

from io import StringIO
from pathlib import Path
from contextlib import redirect_stdout
from datetime import datetime, timezone

from django.contrib import admin
from django.db.models import Count
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.shortcuts import render, redirect
from django.contrib.auth import get_user_model
from django import forms


from signal_webhooks.admin import WebhookAdmin, get_webhook_model

from ..util import htmldecode, urldecode, ansi_to_html

from core.models import Snapshot, ArchiveResult, Tag
from core.forms import AddLinkForm

from core.mixins import SearchResultsAdminMixin
from api.models import APIToken

from index.html import snapshot_icons
from logging_util import printable_filesize
from main import add, remove
from extractors import archive_links
from config import (
    OUTPUT_DIR,
    SNAPSHOTS_PER_PAGE,
    VERSION,
    VERSIONS_AVAILABLE,
    CAN_UPGRADE
)

GLOBAL_CONTEXT = {'VERSION': VERSION, 'VERSIONS_AVAILABLE': VERSIONS_AVAILABLE, 'CAN_UPGRADE': CAN_UPGRADE}

# Admin URLs
# /admin/
# /admin/login/
# /admin/core/
# /admin/core/snapshot/
# /admin/core/snapshot/:uuid/
# /admin/core/tag/
# /admin/core/tag/:uuid/


# TODO: https://stackoverflow.com/questions/40760880/add-custom-button-to-django-admin-panel


class ArchiveBoxAdmin(admin.AdminSite):
    site_header = 'ArchiveBox'
    index_title = 'Links'
    site_title = 'Index'
    namespace = 'admin'

    def get_urls(self):
        return [
            path('core/snapshot/add/', self.add_view, name='Add'),
        ] + super().get_urls()

    def add_view(self, request):
        if not request.user.is_authenticated:
            return redirect(f'/admin/login/?next={request.path}')

        request.current_app = self.name
        context = {
            **self.each_context(request),
            'title': 'Add URLs',
        }

        if request.method == 'GET':
            context['form'] = AddLinkForm()

        elif request.method == 'POST':
            form = AddLinkForm(request.POST)
            if form.is_valid():
                url = form.cleaned_data["url"]
                print(f'[+] Adding URL: {url}')
                depth = 0 if form.cleaned_data["depth"] == "0" else 1
                input_kwargs = {
                    "urls": url,
                    "depth": depth,
                    "update_all": False,
                    "out_dir": OUTPUT_DIR,
                }
                add_stdout = StringIO()
                with redirect_stdout(add_stdout):
                   add(**input_kwargs)
                print(add_stdout.getvalue())

                context.update({
                    "stdout": ansi_to_html(add_stdout.getvalue().strip()),
                    "form": AddLinkForm()
                })
            else:
                context["form"] = form

        return render(template_name='add.html', request=request, context=context)


archivebox_admin = ArchiveBoxAdmin()
archivebox_admin.register(get_user_model())
archivebox_admin.register(APIToken)
archivebox_admin.register(get_webhook_model(), WebhookAdmin)
archivebox_admin.disable_action('delete_selected')


# patch admin with methods to add data views (implemented by admin_data_views package)
from admin_data_views.admin import get_app_list, admin_data_index_view, get_admin_data_urls, get_urls

archivebox_admin.get_app_list = get_app_list.__get__(archivebox_admin, ArchiveBoxAdmin)
archivebox_admin.admin_data_index_view = admin_data_index_view.__get__(archivebox_admin, ArchiveBoxAdmin)
archivebox_admin.get_admin_data_urls = get_admin_data_urls.__get__(archivebox_admin, ArchiveBoxAdmin)
archivebox_admin.get_urls = get_urls(archivebox_admin.get_urls).__get__(archivebox_admin, ArchiveBoxAdmin)


class ArchiveResultInline(admin.TabularInline):
    model = ArchiveResult

class TagInline(admin.TabularInline):
    model = Snapshot.tags.through

from django.contrib.admin.helpers import ActionForm
from django.contrib.admin.widgets import AutocompleteSelectMultiple

class AutocompleteTags:
    model = Tag
    search_fields = ['name']
    name = 'tags'
    remote_field = TagInline

class AutocompleteTagsAdminStub:
    name = 'admin'


class SnapshotActionForm(ActionForm):
    tags = forms.ModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        widget=AutocompleteSelectMultiple(
            AutocompleteTags(),
            AutocompleteTagsAdminStub(),
        ),
    )

    # TODO: allow selecting actions for specific extractors? is this useful?
    # EXTRACTOR_CHOICES = [
    #     (name, name.title())
    #     for name, _, _ in get_default_archive_methods()
    # ]
    # extractor = forms.ChoiceField(
    #     choices=EXTRACTOR_CHOICES,
    #     required=False,
    #     widget=forms.MultileChoiceField(attrs={'class': "form-control"})
    # )


@admin.register(Snapshot, site=archivebox_admin)
class SnapshotAdmin(SearchResultsAdminMixin, admin.ModelAdmin):
    list_display = ('added', 'title_str', 'files', 'size', 'url_str')
    sort_fields = ('title_str', 'url_str', 'added', 'files')
    readonly_fields = ('info', 'pk', 'uuid', 'abid', 'calculate_abid', 'bookmarked', 'added', 'updated')
    search_fields = ('id', 'url', 'timestamp', 'title', 'tags__name')
    fields = ('timestamp', 'url', 'title', 'tags', *readonly_fields)
    list_filter = ('added', 'updated', 'tags', 'archiveresult__status')
    ordering = ['-added']
    actions = ['add_tags', 'remove_tags', 'update_titles', 'update_snapshots', 'resnapshot_snapshot', 'overwrite_snapshots', 'delete_snapshots']
    autocomplete_fields = ['tags']
    inlines = [ArchiveResultInline]
    list_per_page = SNAPSHOTS_PER_PAGE

    action_form = SnapshotActionForm

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        return super().changelist_view(request, extra_context | GLOBAL_CONTEXT)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('grid/', self.admin_site.admin_view(self.grid_view), name='grid')
        ]
        return custom_urls + urls

    def get_queryset(self, request):
        self.request = request
        return super().get_queryset(request).prefetch_related('tags').annotate(archiveresult_count=Count('archiveresult'))

    def tag_list(self, obj):
        return ', '.join(obj.tags.values_list('name', flat=True))

    # TODO: figure out a different way to do this, you cant nest forms so this doenst work
    # def action(self, obj):
    #     # csrfmiddlewaretoken: Wa8UcQ4fD3FJibzxqHN3IYrrjLo4VguWynmbzzcPYoebfVUnDovon7GEMYFRgsh0
    #     # action: update_snapshots
    #     # select_across: 0
    #     # _selected_action: 76d29b26-2a88-439e-877c-a7cca1b72bb3
    #     return format_html(
    #         '''
    #             <form action="/admin/core/snapshot/" method="post" onsubmit="e => e.stopPropagation()">
    #                 <input type="hidden" name="csrfmiddlewaretoken" value="{}">
    #                 <input type="hidden" name="_selected_action" value="{}">
    #                 <button name="update_snapshots">Check</button>
    #                 <button name="update_titles">Pull title + favicon</button>
    #                 <button name="update_snapshots">Update</button>
    #                 <button name="overwrite_snapshots">Re-Archive (overwrite)</button>
    #                 <button name="delete_snapshots">Permanently delete</button>
    #             </form>
    #         ''',
    #         csrf.get_token(self.request),
    #         obj.pk,
    #     )

    def info(self, obj):
        return format_html(
            '''
            PK: <code style="font-size: 10px; user-select: all">{}</code> &nbsp; &nbsp;
            ABID: <code style="font-size: 10px; user-select: all">{}</code> &nbsp; &nbsp;
            UUID: <code style="font-size: 10px; user-select: all">{}</code> &nbsp; &nbsp;
            Timestamp: <code style="font-size: 10px; user-select: all">{}</code> &nbsp; &nbsp;
            URL Hash: <code style="font-size: 10px; user-select: all">{}</code><br/>
            Archived: {} ({} files {}) &nbsp; &nbsp;
            Favicon: <img src="{}" style="height: 20px"/> &nbsp; &nbsp;
            Status code: {} &nbsp; &nbsp;
            Server: {} &nbsp; &nbsp;
            Content type: {} &nbsp; &nbsp;
            Extension: {} &nbsp; &nbsp;
            <br/><br/>
            <a href="/archive/{}">View Snapshot index ➡️</a> &nbsp; &nbsp;
            <a href="/admin/core/snapshot/?uuid__exact={}">View actions ⚙️</a>
            ''',
            obj.pk,
            obj.ABID,
            obj.uuid,
            obj.timestamp,
            obj.url_hash,
            '✅' if obj.is_archived else '❌',
            obj.num_outputs,
            self.size(obj),
            f'/archive/{obj.timestamp}/favicon.ico',
            obj.status_code or '?',
            obj.headers and obj.headers.get('Server') or '?',
            obj.headers and obj.headers.get('Content-Type') or '?',
            obj.extension or '?',
            obj.timestamp,
            obj.uuid,
        )

    @admin.display(
        description='Title',
        ordering='title',
    )
    def title_str(self, obj):
        canon = obj.as_link().canonical_outputs()
        tags = ''.join(
            format_html('<a href="/admin/core/snapshot/?tags__id__exact={}"><span class="tag">{}</span></a> ', tag.id, tag)
            for tag in obj.tags.all()
            if str(tag).strip()
        )
        return format_html(
            '<a href="/{}">'
                '<img src="/{}/{}" class="favicon" onerror="this.remove()">'
            '</a>'
            '<a href="/{}/index.html">'
                '<b class="status-{}">{}</b>'
            '</a>',
            obj.archive_path,
            obj.archive_path, canon['favicon_path'],
            obj.archive_path,
            'fetched' if obj.latest_title or obj.title else 'pending',
            urldecode(htmldecode(obj.latest_title or obj.title or ''))[:128] or 'Pending...'
        ) + mark_safe(f' <span class="tags">{tags}</span>')

    @admin.display(
        description='Files Saved',
        ordering='archiveresult_count',
    )
    def files(self, obj):
        return snapshot_icons(obj)


    @admin.display(
        ordering='archiveresult_count'
    )
    def size(self, obj):
        archive_size = (Path(obj.link_dir) / 'index.html').exists() and obj.archive_size
        if archive_size:
            size_txt = printable_filesize(archive_size)
            if archive_size > 52428800:
                size_txt = mark_safe(f'<b>{size_txt}</b>')
        else:
            size_txt = mark_safe('<span style="opacity: 0.3">...</span>')
        return format_html(
            '<a href="/{}" title="View all files">{}</a>',
            obj.archive_path,
            size_txt,
        )


    @admin.display(
        description='Original URL',
        ordering='url',
    )
    def url_str(self, obj):
        return format_html(
            '<a href="{}"><code style="user-select: all;">{}</code></a>',
            obj.url,
            obj.url,
        )

    def grid_view(self, request, extra_context=None):

        # cl = self.get_changelist_instance(request)

        # Save before monkey patching to restore for changelist list view
        saved_change_list_template = self.change_list_template
        saved_list_per_page = self.list_per_page
        saved_list_max_show_all = self.list_max_show_all

        # Monkey patch here plus core_tags.py
        self.change_list_template = 'private_index_grid.html'
        self.list_per_page = SNAPSHOTS_PER_PAGE
        self.list_max_show_all = self.list_per_page

        # Call monkey patched view
        rendered_response = self.changelist_view(request, extra_context=extra_context)

        # Restore values
        self.change_list_template = saved_change_list_template
        self.list_per_page = saved_list_per_page
        self.list_max_show_all = saved_list_max_show_all

        return rendered_response

    # for debugging, uncomment this to print all requests:
    # def changelist_view(self, request, extra_context=None):
    #     print('[*] Got request', request.method, request.POST)
    #     return super().changelist_view(request, extra_context=None)

    @admin.action(
        description="Pull"
    )
    def update_snapshots(self, request, queryset):
        archive_links([
            snapshot.as_link()
            for snapshot in queryset
        ], out_dir=OUTPUT_DIR)

    @admin.action(
        description="⬇️ Title"
    )
    def update_titles(self, request, queryset):
        archive_links([
            snapshot.as_link()
            for snapshot in queryset
        ], overwrite=True, methods=('title','favicon'), out_dir=OUTPUT_DIR)

    @admin.action(
        description="Re-Snapshot"
    )
    def resnapshot_snapshot(self, request, queryset):
        for snapshot in queryset:
            timestamp = datetime.now(timezone.utc).isoformat('T', 'seconds')
            new_url = snapshot.url.split('#')[0] + f'#{timestamp}'
            add(new_url, tag=snapshot.tags_str())

    @admin.action(
        description="Reset"
    )
    def overwrite_snapshots(self, request, queryset):
        archive_links([
            snapshot.as_link()
            for snapshot in queryset
        ], overwrite=True, out_dir=OUTPUT_DIR)

    @admin.action(
        description="Delete"
    )
    def delete_snapshots(self, request, queryset):
        remove(snapshots=queryset, yes=True, delete=True, out_dir=OUTPUT_DIR)


    @admin.action(
        description="+"
    )
    def add_tags(self, request, queryset):
        tags = request.POST.getlist('tags')
        print('[+] Adding tags', tags, 'to Snapshots', queryset)
        for obj in queryset:
            obj.tags.add(*tags)


    @admin.action(
        description="–"
    )
    def remove_tags(self, request, queryset):
        tags = request.POST.getlist('tags')
        print('[-] Removing tags', tags, 'to Snapshots', queryset)
        for obj in queryset:
            obj.tags.remove(*tags)


        





@admin.register(Tag, site=archivebox_admin)
class TagAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name', 'num_snapshots', 'snapshots', 'id')
    sort_fields = ('id', 'name', 'slug')
    readonly_fields = ('id', 'pk', 'abid', 'calculate_abid', 'num_snapshots', 'snapshots')
    search_fields = ('id', 'name', 'slug')
    fields = (*readonly_fields, 'name', 'slug')
    actions = ['delete_selected']
    ordering = ['-id']

    def num_snapshots(self, tag):
        return format_html(
            '<a href="/admin/core/snapshot/?tags__id__exact={}">{} total</a>',
            tag.id,
            tag.snapshot_set.count(),
        )

    def snapshots(self, tag):
        total_count = tag.snapshot_set.count()
        return mark_safe('<br/>'.join(
            format_html(
                '{} <code><a href="/admin/core/snapshot/{}/change"><b>[{}]</b></a> {}</code>',
                snap.updated.strftime('%Y-%m-%d %H:%M') if snap.updated else 'pending...',
                snap.pk,
                snap.abid,
                snap.url,
            )
            for snap in tag.snapshot_set.order_by('-updated')[:10]
        ) + (f'<br/><a href="/admin/core/snapshot/?tags__id__exact={tag.id}">and {total_count-10} more...<a>' if tag.snapshot_set.count() > 10 else ''))


@admin.register(ArchiveResult, site=archivebox_admin)
class ArchiveResultAdmin(admin.ModelAdmin):
    list_display = ('id', 'start_ts', 'extractor', 'snapshot_str', 'tags_str', 'cmd_str', 'status', 'output_str')
    sort_fields = ('start_ts', 'extractor', 'status')
    readonly_fields = ('id', 'ABID', 'snapshot_str', 'tags_str')
    search_fields = ('id', 'uuid', 'snapshot__url', 'extractor', 'output', 'cmd_version', 'cmd', 'snapshot__timestamp')
    fields = (*readonly_fields, 'snapshot', 'extractor', 'status', 'start_ts', 'end_ts', 'output', 'pwd', 'cmd', 'cmd_version')
    autocomplete_fields = ['snapshot']

    list_filter = ('status', 'extractor', 'start_ts', 'cmd_version')
    ordering = ['-start_ts']
    list_per_page = SNAPSHOTS_PER_PAGE

    @admin.display(
        description='snapshot'
    )
    def snapshot_str(self, result):
        return format_html(
            '<a href="/archive/{}/index.html"><b><code>[{}]</code></b></a><br/>'
            '<small>{}</small>',
            result.snapshot.timestamp,
            result.snapshot.timestamp,
            result.snapshot.url[:128],
        )

    @admin.display(
        description='tags'
    )
    def tags_str(self, result):
        return result.snapshot.tags_str()

    def cmd_str(self, result):
        return format_html(
            '<pre>{}</pre>',
            ' '.join(result.cmd) if isinstance(result.cmd, list) else str(result.cmd),
        )

    def output_str(self, result):
        return format_html(
            '<a href="/archive/{}/{}" class="output-link">↗️</a><pre>{}</pre>',
            result.snapshot.timestamp,
            result.output if (result.status == 'succeeded') and result.extractor not in ('title', 'archive_org') else 'index.html',
            result.output,
        )
