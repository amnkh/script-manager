# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from .forms import RegisterForm, AddRequestForm
from .models import API, Request
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .tasks import run_command
import os
from django.conf import settings
from django.http import HttpResponse, Http404
import zipfile
import StringIO
import pytz


def home(request):
    user_apis = []
    user_groups = ['guest'] + list(request.user.groups.all().values_list('name', flat=True)) # Each user is at least in the guest group. If the user is in other groups too, we append them to the list
    all_apis = API.objects.all()
    for api in all_apis:
        authorized_groups = str(api.authorized_groups).replace(' ', '').split(',')
        if len(set(user_groups).intersection(set(authorized_groups))) > 0:
            user_apis.append(api.id)

    if request.method == 'POST':
        form = AddRequestForm(request.POST)
        if form.is_valid():
            req = form.save(commit=False)
            req.owner = request.user
            req.status = 'processing'
            req.save()

            jsonified_request = {
                'id': req.id,
                'command': req.api_id.command,
                'input_params': req.input_params,
                'num_required_input_params': req.api_id.num_input_params
            }
            run_command.delay(jsonified_request)

            return redirect('requests')

    else:
        form = AddRequestForm()

    form.fields["api_id"].queryset = API.objects.filter(pk__in=user_apis)

    return render(request, 'main/home.html', {'form': form})


def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            form.save()
            username = form.cleaned_data.get('username')
            raw_password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=raw_password)
            login(request, user)
            return redirect('home')
    else:
        form = RegisterForm()
    return render(request, 'main/register.html', {'form': form})


@login_required
def requests(request):
    return render(request, 'main/requests.html')


def ajaxGetRequests(request):
    response = []
    if request.user.id:
        reqs = Request.objects.filter(owner=request.user)
        for req in reqs:
            response.append({
                'request_id': req.id,
                'api_name': req.api_id.name,
                'input_params': req.input_params,
                'date_added': aslocaltimestr(req.date_added),
                'status': dict(Request.REQUEST_STATUS)[req.status]
            })

    return JsonResponse(response, safe=False)


def download_results(request):
    request_id = request.GET.get('request_id')
    req = Request.objects.get(pk=request_id)

    return download_zip(req)


def download_logs(request):
    name_to_file_dict = {
        'stdout': 'std.out',
        'stderr': 'std.err'
    }
    request_id = request.GET.get('request_id')
    std_type = request.GET.get('file')
    file_path = os.path.realpath(settings.WORKING_DIR + str(request_id) + '/' + name_to_file_dict[std_type])
    if os.path.exists(file_path):
        with open(file_path) as fh:
            response = HttpResponse(fh.read(), content_type='application/text')
            response['Content-Disposition'] = 'inline; filename=' + os.path.basename(file_path)
            return response

    raise Http404


def download_zip(req):
    file_names = req.api_id.output_files.replace(' ', '').split(',')

    zip_subdir = "results"
    zip_filename = "%s.zip" % zip_subdir

    # Open StringIO to grab in-memory ZIP contents
    s = StringIO.StringIO()

    # The zip compressor
    zf = zipfile.ZipFile(s, "w")

    for path in file_names:
        # Calculate path for file in zip
        file_path = os.path.realpath(settings.WORKING_DIR + str(req.id) + '/' + path)
        # zip_path = os.path.join(file_path, zip_subdir)

        # Add file, at correct path
        zf.write(file_path, path)

    # Must close zip for all contents to be written
    zf.close()

    # Grab ZIP file from in-memory, make response with correct MIME-type
    resp = HttpResponse(s.getvalue(), content_type = "application/x-zip-compressed")
    # ..and correct content-disposition
    resp['Content-Disposition'] = 'attachment; filename=%s' % zip_filename

    return resp


def ajaxGetAPIDescription(request):
    response = {
        'description': ''
    }
    api_id = request.GET.get('api_id')
    if api_id:
        api = API.objects.get(pk=api_id)
        response['description'] = api.description

    return JsonResponse(response, safe=False)


# The following two functions are used for converting default django datetime to local datetime
def utc_to_local(utc_dt):
    local_tz = pytz.timezone(settings.LOCAL_TIME_ZONE)
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)
    return local_tz.normalize(local_dt)


def aslocaltimestr(utc_dt):
    return utc_to_local(utc_dt).strftime('%Y-%m-%d %H:%M:%S')