#--------------------------------------------------------------------------
# Analysis.py
# This class contains the settings for a binned likelihood analysis.
# Author: Eric Carlson (erccarls@ucsc.edu) 11/20/2014
#--------------------------------------------------------------------------
import numpy as np
import healpy, pyfits
import Tools, Template

class Analysis():
    #--------------------------------------------------------------------
    # Most binning settings follows Calore et al 2014 (1409.0042)
    #--------------------------------------------------------------------

    def __init__(self, E_min=5e2, E_max=5e5, nside=256, gamma=1.45, n_bins=20, prefix_bins=[300,350,400,450,500],
                    phfile  = '/data/fermi_data_1-8-14/photon/lat_ph_merged_ALL_BOTH_2.fits',
                    psfFile = '/data/fermi_data_1-8-14/psf_P7REP_SOURCE_BOTH.fits',
                    expcube = '/data/fermi_data_1-8-14/gtexpcube2_ALL_BOTH'):
        '''
        params:
            E_min:       # Min energy for recursive spectral binning
            E_max:       # Max energt for recursive spectral binning
            n_bins:      # Number of recursive spectal bins
            gamma:       # Power-law index for recursive binning 
            nside:       # Number of healpix spatial bins
            prefix_bins: # manually specified low energy bin edges
         -- Fermitools Input -- 
            phfile:      # Photon file from fermitools
            psfFile:     # Output of gtpsf from fermitools
            expcube:     # Output of gtexpcube2 from fermitools
        '''
        self.E_min      = E_min
        self.E_max      = E_max
        self.nside      = nside
        self.gamma      = gamma
        self.n_bins     = n_bins
        self.phfile     = phfile
        self.psfFile    = psfFile
        self.bin_edges  = prefix_bins
        self.expcube    = expcube
        
        # Currently Unassigned 
        self.binned_data = None  # master list of bin counts. 1st index is spectral, 2nd is pixel_number
        self.mask = None         # Mask. 0 is not analyzed. between 0-1 corresponds to weights. 
        self.templateList = {}   # Dict of analysis templates 

        #--------------------------------------------------------------------
        # Recursively generate bin edges
        Ej = self.E_min
        for j in range(self.n_bins):
            # Eqn 2.3 (1409.0042)
            Ej = (Ej**(1-self.gamma) - (self.E_min**(1-self.gamma)-self.E_max**(1-self.gamma))/self.n_bins)**(1/(1-self.gamma))
            self.bin_edges += [Ej,]
        #--------------------------------------------------------------------


    def BinPhotons(self):
        '''Spatially and Spectrally Bin the Photons'''
        
        # Load Fermi Data
        data = pyfits.open(self.phfile)[1].data

        #--------------------------------------------------------------------
        # Perform Spectral binning
        bin_idx = [] # indices of the photons in each spectral bin.
        for i in range(len(self.bin_edges)-1):
            bin_low, bin_high = self.bin_edges[i], self.bin_edges[i+1]
            idx = np.where( (data['ENERGY']>bin_low) & (data['ENERGY']<bin_high) )[0]
            bin_idx.append(idx)

        # Now for each spectral bin, form the list of healpix pixels.
        self.binned_data = np.zeros(shape=(len(bin_idx), 12*self.nside**2)) 
        for i in range(self.binned_data.shape[0]):
            # Convert sky coords to healpix pixel number 
            idx = bin_idx[i]
            pix = Tools.ang2hpix(data['L'][idx], data['B'][idx], nside=self.nside)
            # count the number of events in each healpix pixel.
            np.add.at(self.binned_data[i],pix,1.)


    def GenSquareMask(self,l_range, b_range,plane_mask=0,merge=False):
        '''
        Generate a square analysis mask (square in glat/glon)
        params: 
            l_range: range for min/max galactic longitude
            b_range: range for min/max galactic latitude
            plane_mask: Masks out |b|<plane_mask
            merge: False will replace the current Analysis.mask.  In case one wants to combine multiple masks, merge=True will apply the or operation between the exisiting and new mask
        returns:
            mask healpix array of dimension nside: 
        '''
        b_min,b_max = b_range
        l_min,l_max = l_range

        mask = np.zeros(shape=12*self.nside**2)
        # Find lat/lon of each healpix pixel
        l_pix, b_pix = Tools.hpix2ang(hpix=np.arange(12*self.nside**2),nside=self.nside)
        # Find elements that are masked
        idx = np.where( ((l_pix<l_max) | (l_pix>(l_min+360)))
                  & (b_pix<b_max) & (b_pix>b_min)
                  & (np.abs(b_pix)>plane_mask) )[0]
        mask[idx] = 1. # Set unmasked elements to 1 
        
        if merge==True: 
            masked_idx = np.where(mask==0)[0]
            self.mask[masked_idx]=0
        else: 
            self.mask = mask
        return mask

    def ApplyIRF(self, hpix, E_min,E_max, noPSF):
        '''
        Apply the Instrument response functions to the input healpix map. This includes the effective area and PSF. These quantities are automatically computed based on the spectral weighted average with spectrum from P7REP_v15 diffuse model.
        params:
            hpix: A healpix array. 
            E_min: low energy boundary
            E_max: high energy boundary
            noPSF: Do not apply the PSF (Exposure only)
        '''
        # Apply the PSF.  This is automatically spectrally weighted
        if noPSF==False:
            hpix = Tools.ApplyGaussianPSF(hpix, E_min, E_max, self.psfFile) 
        # Get l,b for each healpix pixel 
        l,b  = Tools.hpix2ang(np.arange(len(hpix)), nside=self.nside)
        # For each healpix pixel, multiply by the exposure. 
        hpix *= Tools.GetExpMap(E_min, E_max, l, b, expcube=self.expcube)

        return hpix


    def AddPointSourceTemplate(self, pscmap ='gtsrcmap_All_Sources.fits',name='PSC',
                                fixSpectrum=False, fixNorm=False, limits=[0,1e2], value=1,fixed=True,):
        '''
        Adds a point source map to the list of templates.  Cartesian input from gtsrcmaps is then converted to a healpix template.
        params:
            pscmap: The point source map should be the output from gtsrcmaps in cartesian coordinates. 
            name:   Name to use for this template.
            fixSpectrum: If True, the relative normalizations of each energy bin will be held fixed for this template, but the overall normalization is free
            fixNorm:     Fix the overall normalization of this template.  This implies fixSpectrum=True.
            limits:      Specify range of template normalizations.
            value:       Initial value for the template normalization. 
        '''
        # Convert the input map into healpix.
        hpix = Tools.CartesianCountMap2Healpix(cartCube=pscmap,nside=self.nside)[:-1]/1e9        
        for i in range(len(hpix)):
            hpix[i]/=(float(self.bin_edges[i])/self.bin_edges[0])

        self.AddTemplate(name, hpix, fixSpectrum, fixNorm, limits, value,ApplyIRF=False, sourceClass='PSC')



    def PrintTemplates(self):
        '''
        Prints the names and properties of each template in the template list.
        '''
        print '%20s' % 'NAME', '%25s' % 'LIMITS', '%10s' % 'VALUE', '%10s' % 'FIXNORM', '%10s' % 'FIXSPEC', '%10s' % 'SRCCLASS'
        for key in self.templateList:
            temp = self.templateList[key]
            print '%20s' % key, '%25s' % temp.limits, '%10s' % temp.value, '%10s' % temp.fixNorm, '%10s' % temp.fixSpectrum, '%10s' % temp.sourceClass


    def AddTemplate(self,name, healpixCube, fixSpectrum=False, fixNorm=False, limits=[0,1e5], value=1, ApplyIRF=True, sourceClass='GEN'):
        '''
        Add Template to the template list.
        params:
            name:   Name to use for this template.
            healpixCube: Actually a 2-d array with first index selecting energy and second index selecting the healpix index
            fixSpectrum: If True, the relative normalizations of each energy bin will be held fixed for this template, but the overall normalization is free
            fixNorm:     Fix the overall normalization of this template.  This implies fixSpectrum=True.
            limits:      Specify range of template normalizations.
            value:       Initial value for the template normalization.
        '''
        # Error Checking on shape of input cube. 
        if (healpixCube.shape[0] != (len(self.bin_edges)-1)) or (healpixCube.shape[1]!=12*self.nside**2):
            raise(Exception("Shape of template does not match binning"))

        if ApplyIRF==True:
            for i_E in range(len(self.bin_edges)-1):
                if sourceClass == 'ISO':
                    healpixCube[i_E] = self.ApplyIRF(healpixCube[i_E], self.bin_edges[i_E],self.bin_edges[i_E+1],noPSF=True)
                else:
                    healpixCube[i_E] = self.ApplyIRF(healpixCube[i_E], self.bin_edges[i_E],self.bin_edges[i_E+1])


        # Instantiate the template object. 
        template = Template.Template(healpixCube.astype(np.float32), fixSpectrum, fixNorm, limits, value, sourceClass)
        # Add the instance to the master template list.
        self.templateList[name] = template

    def RemoveTemplate(self, name):
        '''
        Removes a template from the template list.
        params:
            name: Name of template to remove.
        '''
        self.templateList.pop(name)

    def AddIsotropicTemplate(self,isofile='iso_clean_v05.txt'):
        '''
        Generates an isotropic template from a spectral file.  The file should be the same format as the isotropic files from fermi (3 columns with E,flux,fluxUnc.)
        '''
        # Load isotropic emission file
        E,flux,fluxUnc = np.genfromtxt(isofile).T
        # Build a power law interpolator
        fluxInterp = lambda x: np.exp(np.interp(np.log(x),np.log(E),np.log(flux)))
        fluxUncInterp = lambda x: np.exp(np.interp(np.log(x),np.log(E),np.log(fluxUnc)))
        
        # Units are in ph/cm^2/s/MeV/sr
        healpixCube = np.ones(shape=(len(self.bin_edges)-1,12*self.nside**2))
        # Solid Angle of each pixel
        solidAngle = 4*np.pi/(12*self.nside**2)
        # Find 
        from scipy.integrate import quad
        for i_E in range(len(self.bin_edges)-1):
            #multiply by integral(flux *dE )*solidangle
            healpixCube[i_E]*=quad(fluxInterp,self.bin_edges[i_E],self.bin_edges[i_E+1])[0]*solidAngle
        
        # Add the template to the list. 
        #IRFs are applied during add template.  This multiplies by cm^2 s
        self.AddTemplate(name='Isotropic', healpixCube=healpixCube, fixSpectrum=True,
                        fixNorm=True, limits=[0,1e5], value=1, ApplyIRF=True, sourceClass='ISO')

        #TODO: NEED TO DEAL WITH UNCERTAINTY VECTOR 


  
        









