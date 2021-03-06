#!/usr/bin/python
# -*- coding: utf-8 -*-

from builtins import str
from builtins import object
from django.http import Http404, JsonResponse,HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template import RequestContext
from compounddb.models import Compound, Tag
from guest.decorators import guest_allowed, login_required
from django.contrib.auth import authenticate, login
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_cookie
from django.forms import ModelForm
from django.contrib import messages
import pybel
import re
from simplejson import dumps
import tempfile
import os
import json
from targetsearch.helpers import getChemblSVG

class compoundForm(ModelForm):

    class Meta(object):

        model = Compound
        fields = ('cid', 'name')


                        # hide private compounds from other users

@guest_allowed
@cache_page(60 * 120)
@vary_on_cookie
def render_image(request, id=None, cid=None, filename=None):

    compound = get_compound(request.user,id,cid)

    if compound.weight > 2000:
        raise Http404
    smiles = re.match(r"^(\S+)", compound.smiles).group(1)
    mymol = pybel.readstring('smi', str(smiles))
    #this simple method below does not work as it returns the image as a unicode string
    # which cannot be converted back to raw bytes
    ##png = mymol.write(format='_png2')

    # so we write the image to a temp file and then read it back as bytes
    fp = tempfile.NamedTemporaryFile()
    fp.close() # deletes the file, but we still use the unique file name
    mymol.write(format='_png2',filename=fp.name)
    imageFile = open(fp.name,"rb")
    image = imageFile.read()
    imageFile.close()
    os.remove(fp.name)
    return HttpResponse(image, content_type='image/png')

# 1d * 24h/d * 60m/h * 60s/m = 86400
# 4w * 7d/w * 24h/d * 60m/h * 60s/m = 2419200

@guest_allowed
@cache_page(60 * 120)
def render_svg(request, id, filename=None):
    compound = get_compound(request.user, id, None)

    if compound.weight > 20000:
        raise Http404("Molecular weight ({}) exceeds 20000 Da".format(compound.weight))
    sdf = compound.sdffile_set.all()[0].sdffile
    mymol = pybel.readstring('mdl', sdf)
    svg = mymol.write(format='svg', opt={'d': ''})

    hr = HttpResponse(svg, content_type='image/svg+xml')
    d = dict()
    d['Cache-Control'] = 'max-age=86400, private'
    d['Expires'] = None
    d['Vary'] = 'Cookie'
    hr.cmt_force_http_headers = d
    return hr

@cache_page(60 * 120)
def render_chembl_svg(request, chembl_id, filename=None):
    try:
        img = getChemblSVG(chembl_id)
        hr = HttpResponse(img, content_type='image/svg+xml')
        d = dict()
        d['Cache-Control'] = 'max-age=86400, public'
        d['Expires'] = None
        d['Vary'] = None
        hr.cmt_force_http_headers = d
        return hr
    except Exception as e:
        raise Http404(str(e))

@guest_allowed
def cid_lookup(request):
    if request.is_ajax():
        try:
            cid = request.GET['cid']
            compound = Compound.objects.get(cid=cid, user=request.user)
            response = dict(id=str(compound.id))
        except:
            response = dict(id='ERROR')
        return HttpResponse(dumps(response), 'text/json')
    else:
        raise Http404

def get_compound(user,id=None,cid=None):
    compound = None
    try:
        if id is not None:
            compound = Compound.objects.get(pk=id, user=user)
        elif cid is not None:
            compound = Compound.objects.get(cid__iexact=cid, user=user)

    except Compound.DoesNotExist:
        raise Http404
    return compound


@guest_allowed
def compound_detail(request, resource=None, id=None, cid=None, filename=None):

    compound = get_compound(request.user,id,cid)

    if request.method == 'POST':
        form = compoundForm(request.POST)
        if form.is_valid():
            compound.cid = form['cid'].value()
            compound.name = form['name'].value()
            compound.smiles = re.match(r"^(\S*)",
                    compound.smiles).group(1) + ' ' + compound.cid
            compound.save()
            sdf = compound.sdffile_set.all()[0]
            sdf.sdffile = compound.cid + '\n' + re.match(r"^.*?\n(.*)$"
                    , sdf.sdffile, flags=re.M | re.S).group(1)
            sdf.save()
            messages.success(request, 'Success: details updated.')
        else:
            messages.error(request, 'Error: invalid form data.')

    inchi = compound.inchi
    smiles = compound.smiles
    sdf = compound.sdffile_set.all()[0].sdffile

    if resource:
        if resource == 'smiles':
            return HttpResponse(smiles, content_type='text/plain')
        elif resource == 'inchi':
            return HttpResponse(inchi, content_type='text/plain')
        elif resource == 'sdf':
            return HttpResponse(sdf, content_type='text/plain')
        elif resource == 'delete':
            compound.delete()
            return HttpResponse('deleted', content_type='text/plain')
        elif resource == 'editform':
            form = compoundForm(instance=compound)
            return render(request,'compounddb/genericForm.html',
                    dict(title='Edit Compound \'' + compound.cid + '\''
                    , form=form))

    return render(request,'compounddb/compound.html', dict(compound=compound,
                              sdf=sdf, smiles=smiles, inchi=inchi))
@guest_allowed
def tagDuplicateCompounds(request):
    from django.db.models import Count
    import itertools
    from operator import itemgetter

    #print("tagging duplicates")
    if request.is_ajax() and request.method == 'GET':
        # group all compounds by inchi and return groups with size > 1
        groupInfo =Compound.objects.values_list('inchi') \
            .annotate(duplicate_count=Count('inchi')) \
            .filter(user=request.user,duplicate_count__gt=1) \
            .order_by('inchi')
        dupedInchis = [row[0] for row in groupInfo ]
        numGroups = len(groupInfo)
        
        #print("duped inchi's: "+str(dupedInchis))

        # get the id and inchi of duplicated compounds
        dupedCompounds = Compound.objects \
                            .values_list("id","inchi") \
                            .filter(user=request.user,inchi__in=dupedInchis)

        #print("duped compounds: "+str(dupedCompounds))
        allIds = [ row[0] for row in dupedCompounds]
        groups = itertools.groupby(sorted(dupedCompounds,key=itemgetter(1)),key=itemgetter(1))
        #print("groups: "+str(groups))

        # tag all group memebers with 'duplicated'
        Tag.ensureAllExist(["duplicate-extra"],request.user)
        extraTag= Tag.objects.filter(user=request.user,name__exact="duplicate-extra").get()
        #print("duplicatedTag: "+str(extraTag))


        # create all tags we'll need at once
        tagNames = [ "duplicate-set-"+str(i) for i in range(numGroups)] 
        Tag.ensureAllExist(tagNames,request.user)
        tags = Tag.objects.filter(user=request.user,name__in=tagNames).order_by("id")

        newTags = {}
        # tag all but the first member of each group with duplicted-X
        for setNum,group in enumerate(groups): # groups is a list of tuples, each tuple is (inchi,[ids])
            ids = [row[0] for row in list(group[1])] 
            #print("group: "+str(ids))
            for i,compound in enumerate(Compound.objects.filter(user=request.user,id__in=ids)):
                t=newTags.setdefault(compound.id,[])
                if i != 0: #don't tag first compound in each group, that will be the one to keep
                    compound.tags.add(extraTag)
                    t.append(extraTag.name)
                compound.tags.add(tags[setNum])
                t.append(tags[setNum].name)
                compound.save()
        return JsonResponse(newTags)


    return JsonResponse({})
@guest_allowed
def tagCompounds(request,action):
    if request.is_ajax() and request.method == 'POST':
        rawJson = request.body
        data = json.loads(rawJson.decode("utf-8"))
        compoundIds = []
        tags = []
        try:
            tags = data["tags"]
            compoundIds = [int(id) for id in data["ids"]]
        except:
            print("failed to parse json for tags and compound ids: "+str(rawJson))
            return HttpResponse("failed to parse JSON",status=404)

        #print("adding tags "+str(tags)+" to compounds "+str(compoundIds))

        Tag.ensureAllExist(tags,request.user)

        tagObjects = Tag.objects.filter(user=request.user,name__in=tags)
        compounds = Compound.objects.filter(user=request.user,id__in=compoundIds)
        for compound in compounds:
            if action == 'add':
                compound.tags.add(*tagObjects)
            elif action == 'remove':
                compound.tags.remove(*tagObjects)
            else:
                return HttpResponse("Unknown action given",status=404)
            compound.save()


    return HttpResponse('')

@guest_allowed
def batchOperation(request,action):
    #print("batch operation, action: "+action)
    if request.method == "POST" and action == "delete" :
        compoundsNotFound=[]
        compoundIds = request.POST.getlist("id[]")
        #print("deleteing compounds "+str(compoundIds))
        for compoundId in compoundIds:
            try:
                compound = Compound.objects.get(id__iexact=compoundId, user=request.user)
                compound.delete()
                #print("compound deleted")
            except Compound.DoesNotExist:
                print("compound "+str(compoundId)+" not found")
                compoundsNotFound.append(compoundId)
        if len(compoundsNotFound) != 0:
            return HttpResponse("Not all compounds could be deleted: "+str(compoundsNotFound),status=404)

    return HttpResponse('')

@guest_allowed
def countCompoundsWithTags(request,tags):
    return HttpResponse(Compound.byTagNames(tags.split(","),request.user).count())


